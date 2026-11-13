// Code block themes matching the hnrly code cards: plum ground, pink keywords.
import { ExpressiveCodeTheme } from '@astrojs/starlight/expressive-code';

const scopes = (map) =>
  Object.entries(map).map(([foreground, scope]) => ({ scope, settings: { foreground } }));

export const hnrlyDark = new ExpressiveCodeTheme({
  name: 'hnrly-dark',
  type: 'dark',
  colors: {
    'editor.background': '#1E0B19',
    'editor.foreground': '#F0E0FD',
    'titleBar.border': '#36182D',
    'editorGroupHeader.tabsBackground': '#1E0B19',
    'tab.activeBackground': '#1E0B19',
    'tab.activeForeground': '#A69EA4',
    'tab.activeBorderTop': '#1E0B19',
    'terminal.background': '#1E0B19',
    'titleBar.activeBackground': '#1E0B19',
    'titleBar.activeForeground': '#A69EA4',
    'panel.border': '#49353F',
  },
  tokenColors: scopes({
    '#8F7F8B': ['comment', 'punctuation.definition.comment'],
    '#E520E9': ['keyword', 'storage', 'storage.type', 'meta.decorator', 'entity.name.function.decorator', 'punctuation.definition.decorator'],
    '#9B77FF': ['entity.name.type', 'entity.name.class', 'support.type', 'support.class', 'entity.other.inherited-class'],
    '#77FFD8': ['string', 'string.quoted', 'punctuation.definition.string'],
    '#FF6550': ['variable.parameter', 'constant.numeric', 'constant.language', 'entity.other.attribute-name'],
    '#FBFFEA': ['entity.name.function', 'support.function', 'meta.function-call'],
  }),
});

export const hnrlyLight = new ExpressiveCodeTheme({
  name: 'hnrly-light',
  type: 'light',
  colors: {
    'editor.background': '#F7F7F8',
    'editor.foreground': '#18181B',
    'titleBar.border': '#EDEDF0',
    'editorGroupHeader.tabsBackground': '#F7F7F8',
    'tab.activeBackground': '#F7F7F8',
    'tab.activeForeground': '#5B5B66',
    'tab.activeBorderTop': '#F7F7F8',
    'terminal.background': '#F7F7F8',
    'titleBar.activeBackground': '#F7F7F8',
    'titleBar.activeForeground': '#5B5B66',
    'panel.border': '#D4D4D8',
  },
  tokenColors: scopes({
    '#6B6B76': ['comment', 'punctuation.definition.comment'],
    '#B312B7': ['keyword', 'storage', 'storage.type', 'meta.decorator', 'entity.name.function.decorator', 'punctuation.definition.decorator'],
    '#5B3FD1': ['entity.name.type', 'entity.name.class', 'support.type', 'support.class', 'entity.other.inherited-class'],
    '#0B7A63': ['string', 'string.quoted', 'punctuation.definition.string'],
    '#B8341F': ['variable.parameter', 'constant.numeric', 'constant.language', 'entity.other.attribute-name'],
    '#18181B': ['entity.name.function', 'support.function', 'meta.function-call'],
  }),
});
