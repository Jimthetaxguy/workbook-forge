// Embedded independent Rust runtime for generated source profile v4.
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug)]
enum V { N(f64), T(String), B(bool), Z, E(String), Ref(Box<V>), Range(Vec<V>) }
fn error(code: &str) -> V { V::E(code.to_string()) }
fn num(value: f64) -> V { if value.is_finite() { V::N(value) } else { error("#NUM!") } }
fn boolean(value: bool) -> V { V::B(value) }
fn blank() -> V { V::Z }
fn unref(mut value: V) -> V {
    while let V::Ref(inner) = value { value = *inner; }
    value
}
fn text_hex(value: &str) -> V {
    assert!(value.len()%2==0,"invalid hex text");
    let bytes: Vec<u8> = value.as_bytes().chunks_exact(2).map(|pair| {
        u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap()
    }).collect();
    V::T(String::from_utf8(bytes).unwrap())
}
fn number(value: V) -> V {
    match unref(value) {
        value @ (V::N(_) | V::E(_)) => value,
        V::Z => num(0.0),
        V::B(value) => num(if value { 1.0 } else { 0.0 }),
        V::T(value) => {
            let text = value.trim();
            if text.is_empty() { return num(0.0); }
            if !text.bytes().all(|c| c.is_ascii_digit() || b".+-eE".contains(&c)) {
                return error("#VALUE!");
            }
            match text.parse::<f64>() { Ok(value) if value.is_finite() => num(value), _ => error("#VALUE!") }
        }
        _ => error("#VALUE!"),
    }
}
fn comparison(left:V,right:V,operator:&str)->V {
    use std::cmp::Ordering;
    let mut left=unref(left); let mut right=unref(right);
    if matches!(left,V::E(_)) {return left;}
    if matches!(right,V::E(_)) {return right;}
    fn empty(other:&V)->V {match other {V::T(_)=>V::T(String::new()),V::B(_)=>V::B(false),_=>V::N(0.0)}}
    if matches!(left,V::Z) {left=empty(&right);}
    if matches!(right,V::Z) {right=empty(&left);}
    fn rank(value:&V)->Option<u8> {match value {V::N(_)=>Some(0),V::T(_)=>Some(1),V::B(_)=>Some(2),_=>None}}
    let (Some(a),Some(b))=(rank(&left),rank(&right)) else{return error("#VALUE!");};
    let order=match (&left,&right) {
        (V::N(a),V::N(b))=>a.partial_cmp(b).expect("finite numbers"),
        (V::B(a),V::B(b))=>a.cmp(b),
        (V::T(a),V::T(b))=>if a.is_ascii() && b.is_ascii(){a.to_ascii_lowercase().cmp(&b.to_ascii_lowercase())}else{a.as_bytes().cmp(b.as_bytes())},
        _=>a.cmp(&b),
    };
    boolean(match operator {"="=>order==Ordering::Equal,"<>"=>order!=Ordering::Equal,"<"=>order==Ordering::Less,"<="=>order!=Ordering::Greater,">"=>order==Ordering::Greater,">="=>order!=Ordering::Less,_=>unreachable!()})
}
fn equal(a:V,b:V)->V {comparison(a,b,"=")}
fn not_equal(a:V,b:V)->V {comparison(a,b,"<>")}
fn less(a:V,b:V)->V {comparison(a,b,"<")}
fn less_equal(a:V,b:V)->V {comparison(a,b,"<=")}
fn greater(a:V,b:V)->V {comparison(a,b,">")}
fn greater_equal(a:V,b:V)->V {comparison(a,b,">=")}
fn binary(left: V, right: V, operator: char) -> V {
    let left = number(left);
    let right = number(right);
    if matches!(left, V::E(_)) { return left; }
    if matches!(right, V::E(_)) { return right; }
    let (V::N(a), V::N(b)) = (left, right) else { unreachable!() };
    if operator == '/' && b == 0.0 { return error("#DIV/0!"); }
    num(match operator { '+' => a+b, '-' => a-b, '*' => a*b, '/' => a/b, _ => unreachable!() })
}
fn add(left: V, right: V) -> V { binary(left, right, '+') }
fn sub(left: V, right: V) -> V { binary(left, right, '-') }
fn mul(left: V, right: V) -> V { binary(left, right, '*') }
fn div(left: V, right: V) -> V { binary(left, right, '/') }
fn neg(value: V) -> V { match number(value) { V::N(value) => num(-value), value => value } }
fn positive(value: V) -> V { number(value) }
fn if_value<Y,N>(condition: V, engine: &mut Engine, yes:Y, no:N) -> V
where Y:FnOnce(&mut Engine)->V, N:FnOnce(&mut Engine)->V {
    let truth=match unref(condition) {
        value @ V::E(_) => return value,
        V::N(value) => value != 0.0, V::B(value) => value, V::Z => false,
        V::T(value) if value.is_empty() => false,
        _ => return error("#VALUE!"),
    };
    unref(if truth { yes(engine) } else { no(engine) })
}
fn if_error<F>(value: V, engine: &mut Engine, fallback:F) -> V
where F:FnOnce(&mut Engine)->V {
    let result=unref(if matches!(unref(value.clone()),V::E(_)) { fallback(engine) } else { value });
    match result { V::Z=>V::T(String::new()),value=>value }
}
fn resolve(engine: &Engine, value:&str) -> String {
    let prefix=if let Some((name,_))=value.rsplit_once('!') {
        let name=if name.starts_with('\'') && name.ends_with('\'') { name[1..name.len()-1].replace("''", "'") } else { name.to_string() };
        format!("'{}'!",engine.sheets[&name.to_lowercase()].replace('\'',"''"))
    } else { engine.context.clone() };
    format!("{prefix}{}",address(value))
}
fn address(value: &str) -> String { value.rsplit('!').next().unwrap().replace('$', "").to_ascii_uppercase() }
fn coordinates(value: &str) -> (usize, usize) {
    let value = address(value);
    let first_digit = value.find(|c: char| c.is_ascii_digit()).unwrap();
    let column = value[..first_digit].bytes().fold(0, |column,c| column*26 + (c-b'A'+1) as usize);
    (column, value[first_digit..].parse().unwrap())
}
fn column_name(mut column: usize) -> String {
    let mut letters = Vec::new();
    while column > 0 { column -= 1; letters.push(b'A' + (column%26) as u8); column /= 26; }
    letters.reverse(); String::from_utf8(letters).unwrap()
}
fn reference(engine: &mut Engine, value: &str) -> V { let item=engine.get(&resolve(engine,value)); engine.account(&item); V::Ref(Box::new(item)) }
fn value_bytes(value: &V) -> usize {
    match value { V::T(value) | V::E(value) => value.len(), V::Ref(value) => value_bytes(value), V::N(_) => 8, _ => 1 }
}
fn range_ref(engine: &mut Engine, value: &str) -> V {
    let (start, end) = value.split_once(':').unwrap();
    let a = coordinates(start); let b = coordinates(end);
    assert!((a.0.abs_diff(b.0)+1)*(a.1.abs_diff(b.1)+1) <= 100000, "resource_limit: range");
    let resolved=resolve(engine,start);
    let prefix=resolved.rsplit_once('!').unwrap().0;
    let mut values = Vec::new();
    let mut size = 0;
    for row in a.1.min(b.1)..=a.1.max(b.1) {
        for column in a.0.min(b.0)..=a.0.max(b.0) {
            let item=engine.get(&format!("{prefix}!{}{row}",column_name(column)));
            engine.account(&item);
            size+=value_bytes(&item);
            assert!(size<=1024*1024,"resource_limit: range payload exceeds 1 MiB");
            values.push(item);
        }
    }
    V::Range(values)
}
fn sum_values(arguments: Vec<V>) -> V {
    if !(1..=255).contains(&arguments.len()) { return error("#VALUE!"); }
    let mut numbers = Vec::new();
    for argument in arguments {
        let referenced = matches!(argument, V::Ref(_) | V::Range(_));
        let values = if let V::Range(values) = argument { values } else { vec![argument] };
        for item in values {
            let item = unref(item);
            if matches!(item, V::E(_)) { return item; }
            if referenced && !matches!(item,V::N(_)) { continue; }
            match number(item) { V::N(value) => numbers.push(value), value => return value }
        }
    }
    // Same ordered +0.0 left fold as the independent Python runtime.
    num(numbers.iter().fold(0.0, |total, value| total + value))
}
struct Engine { sheets:BTreeMap<String,String>, context:String, defaults: BTreeMap<String,V>, cache: BTreeMap<String,V>, active: BTreeSet<String>, cache_bytes: usize, materialized_bytes: usize }
impl Engine {
    fn account(&mut self, value:&V) { self.materialized_bytes+=value_bytes(value); assert!(self.materialized_bytes<=1024*1024,"resource_limit: materialized reference payload exceeds 1 MiB"); }
    fn get(&mut self, key: &str) -> V {
        if let Some(value) = self.cache.get(key) { return value.clone(); }
        if !is_formula(key) { return self.defaults.get(key).cloned().unwrap_or(V::Z); }
        assert!(!self.active.contains(key), "cycle: iterative calculation is not enabled");
        assert!(self.active.len()<96, "resource_limit: dependency depth exceeds 96");
        self.active.insert(key.to_string());
        let previous=std::mem::replace(&mut self.context,format!("{}!",key.rsplit_once('!').unwrap().0));
        let computed=formula(key,self);
        self.context=previous;
        let value = match unref(computed) { V::Z => num(0.0), V::Range(_) => error("#VALUE!"), value => value };
        self.active.remove(key);
        self.cache_bytes+=value_bytes(&value);
        assert!(self.cache_bytes<=1024*1024,"resource_limit: cached payload exceeds 1 MiB");
        self.cache.insert(key.to_string(),value.clone());
        value
    }
}
fn decode(value: &str) -> V {
    let (tag,payload) = value.split_once(':').expect("typed input");
    match tag {
        "n" => num(payload.parse().expect("number input")),
        "t" => text_hex(payload),
        "b" if payload=="0" || payload=="1" => boolean(payload=="1"),
        "z" if payload.is_empty() => blank(),
        "e" if ["#DIV/0!","#VALUE!","#REF!","#NAME?","#NUM!","#N/A","#CALC!","#NULL!","#SPILL!"].contains(&payload) => error(payload),
        _ => panic!("invalid typed input"),
    }
}
fn run(mut defaults: BTreeMap<String,V>, sheets:&[&str]) {
    let mut arguments: Vec<String> = std::env::args().skip(1).collect();
    if arguments == ["--stdin"] {
        use std::io::Read;
        let mut bytes=Vec::new();
        std::io::stdin().take(1024*1024+1).read_to_end(&mut bytes).expect("input");
        assert!(bytes.len()<=1024*1024,"resource_limit: input exceeds 1 MiB");
        arguments=String::from_utf8(bytes).expect("UTF-8").lines().map(str::to_string).collect();
    }
    for argument in arguments {
        let (key,payload)=argument.split_once('=').expect("input override");
        let key=match text_hex(key) { V::T(value)=>value,_=>unreachable!() };
        assert!(defaults.contains_key(&key), "unknown input address");
        defaults.insert(key,decode(payload));
    }
    let mut keys: BTreeSet<String> = defaults.keys().cloned().collect();
    keys.extend(formula_keys().iter().map(|s|s.to_string()));
    let mut engine=Engine { sheets:sheets.iter().map(|name|(name.to_lowercase(),name.to_string())).collect(), context:String::new(), defaults, cache:BTreeMap::new(),active:BTreeSet::new(), cache_bytes:0, materialized_bytes:0 };
    let mut output_bytes=0;
    for key in keys {
        let (tag,payload)=match unref(engine.get(&key)) {
            V::N(value) => ("n",value.to_string()), V::B(value) => ("b",if value{"1".into()}else{"0".into()}),
            V::T(value) => ("t",value.bytes().map(|b|format!("{b:02x}")).collect()),
            V::Z => ("z",String::new()), V::E(value) => ("e",value), _ => unreachable!(),
        };
        let encoded_key:String=key.bytes().map(|b|format!("{b:02x}")).collect();
        let line=format!("{encoded_key}\t{tag}\t{payload}");
        output_bytes+=line.len()+1;
        assert!(output_bytes<=1024*1024,"resource_limit: output exceeds 1 MiB");
        println!("{line}");
    }
}
