// Univer as a presentation/editing layer only. Five modes are built
// here so the spike can compare them; the adapter is the same in all of them.
//
//   univer-default      formula plugins registered, stock config (control:
//                       Univer is expected to calculate)
//   load-only           sheets-formula initialFormulaComputing = NO_CALCULATION
//   not-execute         load-only + engine-formula and sheets notExecuteFormula
//   engine-only         UniverFormulaEnginePlugin { notExecuteFormula } but no
//                       sheets-formula(-ui); sheets notExecuteFormula = true
//   no-formula-plugins  no UniverFormulaEnginePlugin / sheets-formula(-ui);
//                       sheets notExecuteFormula = true

import { LocaleType, Univer, UniverInstanceType, mergeLocales } from '@univerjs/core';
import { defaultTheme } from '@univerjs/themes';
import { FUniver } from '@univerjs/core/facade';
import { UniverDocsPlugin } from '@univerjs/docs';
import { UniverDocsUIPlugin } from '@univerjs/docs-ui';
import { UniverRenderEnginePlugin } from '@univerjs/engine-render';
import { UniverSheetsPlugin } from '@univerjs/sheets';
import { UniverSheetsUIPlugin } from '@univerjs/sheets-ui';
import { UniverUIPlugin } from '@univerjs/ui';
import type { FunctionHelp } from './formula-plugins';
import '@univerjs/sheets/facade';
import '@univerjs/ui/facade';
import '@univerjs/docs-ui/facade';
import '@univerjs/sheets-ui/facade';
import DesignEnUS from '@univerjs/design/locale/en-US';
import UIEnUS from '@univerjs/ui/locale/en-US';
import DocsUIEnUS from '@univerjs/docs-ui/locale/en-US';
import SheetsEnUS from '@univerjs/sheets/locale/en-US';
import SheetsUIEnUS from '@univerjs/sheets-ui/locale/en-US';
import '@univerjs/design/lib/index.css';
import '@univerjs/ui/lib/index.css';
import '@univerjs/docs-ui/lib/index.css';
import '@univerjs/sheets-ui/lib/index.css';

export const MODES = ['univer-default', 'load-only', 'not-execute', 'engine-only', 'no-formula-plugins'] as const;
export type Mode = (typeof MODES)[number];

export interface UniverHost {
  univer: Univer;
  univerAPI: FUniver;
  mode: Mode;
  formulaPlugins: boolean;
  /**
   * Replace Univer's function suggestions and argument help. Returns how many
   * of Univer's own entries were removed; 0 when no formula plugins are loaded.
   */
  setFunctionHelp(help: FunctionHelp[]): number;
}

/**
 * Univer's stock theme with the shell's accent and warm grays (src/shell.css
 * carries the same values as --accent-* and --gray-*), so the selection, the
 * headers and the sheet bar match the frame around them.
 */
export const forgeTheme = {
  ...defaultTheme,
  primary: {
    50: '#FFF6EF',
    100: '#FFE9D9',
    200: '#FCD0B0',
    300: '#F7AE7E',
    400: '#EE8548',
    500: '#DD6524',
    600: '#C4501A',
    700: '#A13F15',
    800: '#7F3314',
    900: '#662B13',
  },
  gray: {
    ...defaultTheme.gray,
    50: '#FAF9F7',
    100: '#F2F0EC',
    200: '#E7E4DF',
    300: '#B5AFA6',
    400: '#8A847A',
    500: '#5E5850',
    600: '#3A3530',
    700: '#2B2724',
    800: '#211E1B',
    900: '#1A1816',
  },
};

export interface HostOptions {
  darkMode?: boolean;
}

export async function createUniverHost(container: HTMLElement, mode: Mode, options: HostOptions = {}): Promise<UniverHost> {
  const formulaPlugins = mode !== 'no-formula-plugins';
  const formula = formulaPlugins ? await import('./formula-plugins') : null;
  const locales = [DesignEnUS, UIEnUS, DocsUIEnUS, SheetsEnUS, SheetsUIEnUS, ...(formula?.locales ?? [])];
  const univer = new Univer({
    theme: forgeTheme,
    darkMode: options.darkMode === true,
    locale: LocaleType.EN_US,
    locales: { [LocaleType.EN_US]: mergeLocales(...locales) },
  });
  // Same order as Univer's own sheets-core preset.
  univer.registerPlugin(UniverDocsPlugin);
  univer.registerPlugin(UniverRenderEnginePlugin);
  // Univer's toolbar and right-click menu are mostly controls Forge refuses
  // (formatting, paste, insert and delete), so the shell shows its own.
  univer.registerPlugin(UniverUIPlugin, { container, toolbar: false, contextMenu: false });
  univer.registerPlugin(UniverDocsUIPlugin);
  if (formula) formula.registerEngine(univer, mode);
  univer.registerPlugin(UniverSheetsPlugin, {
    notExecuteFormula: mode === 'not-execute' || mode === 'engine-only' || mode === 'no-formula-plugins',
  });
  // The selection totals come from Forge's values (the shell's status bar),
  // not Univer's statistic bar, and sheets cannot be added yet.
  univer.registerPlugin(UniverSheetsUIPlugin, {
    footer: { sheetBar: true, statisticBar: false, menus: false, zoomSlider: true, addSheetButtonConfig: { show: false } },
  });
  if (formula) formula.registerSheetsFormula(univer, mode);
  const univerAPI = FUniver.newAPI(univer);
  return { univer, univerAPI, mode, formulaPlugins, setFunctionHelp: (help) => formula?.setFunctionHelp(univer, help) ?? 0 };
}

export { UniverInstanceType };
