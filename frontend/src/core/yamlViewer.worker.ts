import { buildYamlView } from './yamlViewer';

self.onmessage = (event: MessageEvent<string>) => {
  self.postMessage(buildYamlView(event.data));
};
