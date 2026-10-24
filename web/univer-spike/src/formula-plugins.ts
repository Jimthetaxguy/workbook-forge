// Univer's formula stack, loaded only by the modes that register it, so the
// bundle split shows what dropping it saves.

import type { Univer } from '@univerjs/core';
import { UniverFormulaEnginePlugin } from '@univerjs/engine-formula';
import { CalculationMode, UniverSheetsFormulaPlugin } from '@univerjs/sheets-formula';
import { UniverSheetsFormulaUIPlugin } from '@univerjs/sheets-formula-ui';
import '@univerjs/engine-formula/facade';
import '@univerjs/sheets-formula/facade';
import SheetsFormulaEnUS from '@univerjs/sheets-formula/locale/en-US';
import SheetsFormulaUIEnUS from '@univerjs/sheets-formula-ui/locale/en-US';
import '@univerjs/sheets-formula-ui/lib/index.css';
import type { Mode } from './univer-host';

export const locales = [SheetsFormulaEnUS, SheetsFormulaUIEnUS];

export function registerEngine(univer: Univer, mode: Mode) {
  univer.registerPlugin(UniverFormulaEnginePlugin, { notExecuteFormula: mode === 'not-execute' });
}

export function registerSheetsFormula(univer: Univer, mode: Mode) {
  // sheets-formula's own notExecuteFormula expects Univer's RPC worker
  // (IRPCChannelService); that is Univer's worker mode, not a "don't calculate"
  // switch, so it is left unset and initialFormulaComputing is used instead.
  univer.registerPlugin(UniverSheetsFormulaPlugin, {
    initialFormulaComputing: mode === 'univer-default' ? CalculationMode.WHEN_EMPTY : CalculationMode.NO_CALCULATION,
  });
  univer.registerPlugin(UniverSheetsFormulaUIPlugin);
}
