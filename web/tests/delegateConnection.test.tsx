import { expect, test, mock } from 'bun:test';
import { renderToStaticMarkup } from 'react-dom/server';
mock.module('../src/lib/api', () => ({ api: {} }));
const { DelegateEditor } = await import('../src/plugins/settings-panel/DelegatesSettings');
import type { DelegateTypeSpec } from '../src/lib/api';

const specs: DelegateTypeSpec[] = [{
  type: 'a2a', label: 'A2A', blurb: 'Agent',
  capabilities: { stream: true, inject: false, session: false, oneshot: true },
  fields: [
    { key: 'auth.credentials', label: 'Access token', kind: 'secret', required: false },
    { key: 'auth.credentialsEnv', label: 'Environment variable', kind: 'secret-env', required: false },
  ],
}];

test('saved connection masks tokens and puts environment setup under advanced', () => {
  const html = renderToStaticMarkup(<DelegateEditor
    state={{ kind: 'edit', original: {
      name: 'hub', type: 'a2a', description: 'Brain', url: 'http://localhost:7870/a2a',
      configured: true, auth: { scheme: 'bearer', credentials: '__redacted__' },
    } }}
    specs={specs} onCancel={() => {}} onSaved={() => {}}
  />);
  expect(html).toContain('type="password"');
  expect(html).toContain('Token saved');
  expect(html).not.toContain('__redacted__');
  expect(html).toContain('<details');
  expect(html).toContain('Advanced: environment variables');
  expect(html).toContain('Save and connect');
  expect(html).toContain('Remove saved token');
});
