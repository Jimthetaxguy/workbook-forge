// Univer as a presentation/editing layer only. Four configurations are built
// here so the spike can compare them; the adapter is the same in all of them.
//
//   univer-default      formula plugins registered, stock config (control:
//                       Univer is expected to calculate)
//   load-only           sheets-formula initialFormulaComputing = NO_CALCULATION
//   not-execute         load-only + engine-formula and sheets notExecuteFormula
//   no-formula-plugins  no UniverFormulaEnginePlugin / sheets-formula(-ui);
//                       sheets notExecuteFormula = true

import { LocaleType, Univer, UniverInstanceType, mergeLocales } from '@univerjs/core';
import { FUniver } from '@univerjs/core/facade';
import { UniverDocsPlugin } from '@univerjs/docs';
import { UniverDocsUIPlugin } from '@univerjs/docs-ui';
import { UniverRenderEnginePlugin } from '@univerjs/engine-render';
import { UniverSheetsPlugin } from '@univerjs/sheets';
import { UniverSheetsUIPlugin } from '@univerjs/sheets-ui';
import { UniverUIPlugin } from '@univerjs/ui';
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

export const MODES = ['univer-default', 'load-only', 'not-execute', 'no-formula-plugins'] as const;
export type Mode = (typeof MODES)[number];

export interface UniverHost {
  univer: Univer;
  univerAPI: FUniver;
  mode: Mode;
  formulaPlugins: boolean;
}

export async function createUniverHost(container: HTMLElement, mode: Mode): Promise<UniverHost> {
  const formulaPlugins = mode !== 'no-formula-plugins';
  const formula = formulaPlugins ? await import('./formula-plugins') : null;
  const locales = [DesignEnUS, UIEnUS, DocsUIEnUS, SheetsEnUS, SheetsUIEnUS, ...(formula?.locales ?? [])];
  const univer = new Univer({
    locale: LocaleType.EN_US,
    locales: { [LocaleType.EN_US]: mergeLocales(...locales) },
  });
  // Same order as Univer's own sheets-core preset.
  univer.registerPlugin(UniverDocsPlugin);
  univer.registerPlugin(UniverRenderEnginePlugin);
  univer.registerPlugin(UniverUIPlugin, { container });
  univer.registerPlugin(UniverDocsUIPlugin);
  if (formula) formula.registerEngine(univer, mode);
  univer.registerPlugin(UniverSheetsPlugin, {
    notExecuteFormula: mode === 'not-execute' || mode === 'no-formula-plugins',
  });
  univer.registerPlugin(UniverSheetsUIPlugin);
  if (formula) formula.registerSheetsFormula(univer, mode);
  const univerAPI = FUniver.newAPI(univer);
  return { univer, univerAPI, mode, formulaPlugins };
}

export { UniverInstanceType };
