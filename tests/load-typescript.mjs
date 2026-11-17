import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// Pure helpers contain type-only imports; transpile with the existing compiler
// so these tests also run on the project's supported Node.js 20 runtime.
export async function loadTypescript(url) {
  const source = await readFile(url, 'utf8')
  const { outputText } = ts.transpileModule(source, {
    fileName: url.pathname,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
  })
  const resolved = outputText.replace(/from ["'](lucide-react|react\/jsx-runtime)["']/g,
    (_match, name) => `from ${JSON.stringify(import.meta.resolve(name))}`)
  return import(`data:text/javascript;base64,${Buffer.from(resolved).toString('base64')}`)
}
