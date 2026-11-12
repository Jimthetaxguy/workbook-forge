/// <reference types="vite/client" />
declare module '*.xlsx?inline' {
  const dataUrl: string;
  export default dataUrl;
}
declare module 'virtual:forge-catalog' {
  const catalog: {
    /** catalog/formulas.json catalog_version. */
    version: string;
    /** Functions in Microsoft's inventory (catalog/function_inventory.json). */
    inventoried: number;
    /** Functions both engines implement. `max` 254 or 255 means "as many as Excel allows". */
    functions: { name: string; category: string; min: number; max: number | null }[];
  };
  export default catalog;
}
