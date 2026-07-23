/**
 * Serve Monaco from our own origin instead of a third-party CDN.
 *
 * `@monaco-editor/react` defaults to fetching the Monaco runtime from
 * cdn.jsdelivr.net when the editor first mounts. That put a timed, high-stakes
 * assessment behind someone else's uptime and behind whatever egress rules a
 * candidate's employer runs — banks and insurers routinely block third-party
 * CDNs outright. When the fetch fails the loader only logs to the console, so
 * the candidate sits on "Loading editor..." forever with nothing to act on.
 *
 * Importing Monaco and handing the instance to `loader.config` means the
 * runtime ships in our own lazy chunk and `loader.init()` resolves without a
 * single network request, which is what lets the assessment CSP drop its
 * third-party allowlist entirely.
 */
import { loader } from '@monaco-editor/react';

// `monaco-editor` resolves to editor.main, which registers every language
// Monaco ships. Take the editor API plus only the languages the assessment
// workspace can actually open — see languageFromPath in
// assessmentRuntimeHelpers.js — and the two language services that back them.
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';
import * as jsonLanguage from 'monaco-editor/esm/vs/language/json/monaco.contribution';
import * as typescriptLanguage from 'monaco-editor/esm/vs/language/typescript/monaco.contribution';
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution';
import 'monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution';
import 'monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution';
import 'monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution';
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution';
import 'monaco-editor/esm/vs/basic-languages/shell/shell.contribution';

import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import JsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker';
import TsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';

// Language services that back a dedicated worker. Everything else the
// workspace can open — python, markdown, yaml, shell, plaintext — is
// tokenizer-only and served by the default editor worker.
const LANGUAGE_WORKERS = {
  json: JsonWorker,
  typescript: TsWorker,
  javascript: TsWorker,
};

// editor.main hangs these namespaces off `languages` as part of wiring the
// language services up. We import the services directly, so do the same —
// otherwise monaco.languages.typescript and .json are simply absent, which is
// the API the CDN build exposed and the one worker diagnostics reach for.
monaco.languages.json = jsonLanguage;
monaco.languages.typescript = typescriptLanguage;

// Read lazily by Monaco on first worker request, so assigning it here (after
// the import above has already evaluated) is early enough.
globalThis.MonacoEnvironment = {
  getWorker(_workerId, label) {
    const Worker = LANGUAGE_WORKERS[label] || EditorWorker;
    return new Worker();
  },
};

// The AMD loader used to publish this global as a side effect of loading from
// the CDN. Keep it: the assessment smoke check asserts against it, and it is
// the only handle onto a live editor from outside React. Assigned explicitly
// rather than via `MonacoEnvironment.globalAPI`, which would only take effect
// if it were set before the `monaco-editor` import evaluated.
globalThis.monaco = monaco;

loader.config({ monaco });

export default monaco;
