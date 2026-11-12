// Univer's formula stack, loaded only by the modes that register it, so the
// bundle split shows what dropping it saves. The number-format renderer lives
// here too because it imports the formula engine package.

import { BooleanNumber, type Univer } from '@univerjs/core';
import { FunctionType, IDescriptionService, UniverFormulaEnginePlugin } from '@univerjs/engine-formula';
import { CalculationMode, UniverSheetsFormulaPlugin } from '@univerjs/sheets-formula';
import { UniverSheetsFormulaUIPlugin } from '@univerjs/sheets-formula-ui';
import { UniverSheetsNumfmtPlugin } from '@univerjs/sheets-numfmt';
import '@univerjs/engine-formula/facade';
import '@univerjs/sheets-formula/facade';
import SheetsFormulaEnUS from '@univerjs/sheets-formula/locale/en-US';
import SheetsFormulaUIEnUS from '@univerjs/sheets-formula-ui/locale/en-US';
import '@univerjs/sheets-formula-ui/lib/index.css';
import type { Mode } from './univer-host';

export const locales = [SheetsFormulaEnUS, SheetsFormulaUIEnUS];

export function registerEngine(univer: Univer, mode: Mode) {
  univer.registerPlugin(UniverFormulaEnginePlugin, { notExecuteFormula: mode === 'not-execute' || mode === 'engine-only' });
}

export function registerSheetsFormula(univer: Univer, mode: Mode) {
  // Draws a cell's number format ("12.75%", "2024-02-29"). Display only: the
  // stored value stays the number Forge holds.
  univer.registerPlugin(UniverSheetsNumfmtPlugin);
  if (mode === 'engine-only') return;
  // sheets-formula's own notExecuteFormula expects Univer's RPC worker
  // (IRPCChannelService); that is Univer's worker mode, not a "don't calculate"
  // switch, so it is left unset and initialFormulaComputing is used instead.
  univer.registerPlugin(UniverSheetsFormulaPlugin, {
    initialFormulaComputing: mode === 'univer-default' ? CalculationMode.WHEN_EMPTY : CalculationMode.NO_CALCULATION,
  });
  univer.registerPlugin(UniverSheetsFormulaUIPlugin);
}

/** What the in-cell suggestion list and argument help say about one function. */
export interface FunctionHelp {
  name: string;
  summary: string;
  detail: string;
  args: { name: string; optional: boolean; repeat: boolean }[];
}

/**
 * Replace Univer's function suggestions and argument help with `help`.
 * Univer knows several hundred functions and describes them in its own words;
 * in the modes where Forge is the calculator, suggesting one Forge does not
 * implement would invite a formula that stays blank, and Univer's text does
 * not state Forge's limits. Help only: a typed formula still goes to Forge
 * whatever its function names are. Defined names and tables keep their entries.
 */
export function setFunctionHelp(univer: Univer, help: FunctionHelp[]): number {
  const descriptions = univer.__getInjector().get(IDescriptionService);
  const types = new Map<string, FunctionType>();
  const drop: string[] = [];
  for (const [name, info] of descriptions.getDescriptions()) {
    if (info.functionType === FunctionType.DefinedName || info.functionType === FunctionType.Table) continue;
    types.set(String(name).toUpperCase(), info.functionType);
    drop.push(String(name));
  }
  descriptions.unregisterDescriptions(drop);
  descriptions.registerDescriptions(
    help.map((fn) => ({
      functionName: fn.name,
      functionType: types.get(fn.name) ?? FunctionType.Univer,
      description: fn.detail,
      abstract: fn.summary,
      functionParameter: fn.args.map((arg) => ({
        name: arg.name,
        detail: arg.optional ? 'Optional.' : 'Required.',
        example: '',
        require: arg.optional ? BooleanNumber.FALSE : BooleanNumber.TRUE,
        repeat: arg.repeat ? BooleanNumber.TRUE : BooleanNumber.FALSE,
      })),
    })),
  );
  return drop.length;
}
