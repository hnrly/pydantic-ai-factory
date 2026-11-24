import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

// A busy CI machine can take seconds to render a view for the first time, so a
// test waits up to 10 seconds for what it expects, not Testing Library's 1.
configure({ asyncUtilTimeout: 10_000 });

afterEach(() => {
  cleanup();
  localStorage.clear();
  window.location.hash = '';
});
