/**
 * DeepSeek Harness (dsh) integration for JupyterLab.
 *
 * Adds a "deepseek-harness" card to the Launcher (the row that also holds
 * Terminal) that opens the dsh web GUI in a main-area tab.  The GUI is
 * served same-origin through the `jupyterlab_dsh` server extension at
 * `<base_url>dsh/`, so cookies, WebSockets and the dsh Host/Origin fence all
 * work regardless of the address JupyterLab was reached on.
 */
import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { MainAreaWidget } from '@jupyterlab/apputils';

import { ILauncher } from '@jupyterlab/launcher';

import { LabIcon } from '@jupyterlab/ui-components';

import { PageConfig, URLExt } from '@jupyterlab/coreutils';

import { Widget } from '@lumino/widgets';

import { dshIconSvg } from './dsh-icon';

import '../style/index.css';

const PLUGIN_ID = 'jupyterlab-dsh:plugin';
const WIDGET_ID = 'dsh-main-widget';
const COMMAND_ID = 'dsh:open-webui';

export const dshIcon = new LabIcon({
  name: 'dsh:logo',
  svgstr: dshIconSvg
});

/**
 * Find the singleton dsh tab among the main-area widgets.
 *
 * lumino v1 (JupyterLab 3) iterators return `T | undefined` from next(),
 * while lumino v2 (JupyterLab 4) returns an `{value, done}` IteratorResult.
 * Assuming either single contract here spins the UI thread forever on the
 * other JupyterLab generation (the "click freezes the whole page" bug), so
 * both are handled and the scan is capped as a belt-and-braces guard.
 */
function findExistingDshWidget(app: JupyterFrontEnd): Widget | undefined {
  const iter = app.shell.widgets('main');
  for (let guard = 0; guard < 4096; guard++) {
    let cur: any = iter.next();
    if (cur === undefined || cur === null) {
      return undefined; // v1: exhausted
    }
    if (typeof cur === 'object' && 'done' in cur) {
      if (cur.done) {
        return undefined; // v2: exhausted
      }
      cur = cur.value;
      if (cur === undefined || cur === null) {
        return undefined;
      }
    }
    if ((cur as Widget).id === WIDGET_ID) {
      return cur as Widget;
    }
  }
  return undefined;
}

function createDshWidget(): MainAreaWidget {
  const iframe = document.createElement('iframe');
  iframe.className = 'jp-dsh-iframe';
  iframe.title = 'deepseek-harness';
  iframe.src = URLExt.join(PageConfig.getBaseUrl(), 'dsh/');
  iframe.setAttribute('allow', 'clipboard-read; clipboard-write');

  const content = new Widget({ node: iframe });
  content.id = 'dsh-iframe-host';

  const widget = new MainAreaWidget({ content });
  widget.id = WIDGET_ID;
  widget.title.label = 'deepseek-harness';
  widget.title.caption = 'DeepSeek Harness (dsh) web GUI';
  widget.title.closable = true;
  widget.title.icon = dshIcon;
  widget.addClass('jp-dsh-widget');
  return widget;
}

const plugin: JupyterFrontEndPlugin<void> = {
  id: PLUGIN_ID,
  autoStart: true,
  optional: [ILauncher],
  activate: (app: JupyterFrontEnd, launcher: ILauncher | null) => {
    console.log('jupyterlab-dsh extension activated');

    app.commands.addCommand(COMMAND_ID, {
      label: 'deepseek-harness',
      caption: 'Open the DeepSeek Harness (dsh) web GUI',
      icon: dshIcon,
      execute: () => {
        // Singleton tab: reveal an already-open dsh tab instead of stacking.
        if (findExistingDshWidget(app)) {
          app.shell.activateById(WIDGET_ID);
          return;
        }
        const widget = createDshWidget();
        app.shell.add(widget, 'main', { mode: 'tab-after' });
        app.shell.activateById(widget.id);
      }
    });

    if (launcher) {
      // Same section as the Terminal card ("Other"), right beside it.
      launcher.add({
        command: COMMAND_ID,
        category: 'Other',
        rank: 1.01
      });
    }
  }
};

export default plugin;
