// Blog post registry. Each post's body is the markdown file imported raw
// (Vite `?raw`); metadata lives here so the index + hero render without
// parsing YAML frontmatter at runtime. Add a new post = drop a .md in
// ./posts and append an entry here.
import aiNativeRaw from './posts/ai-native-coding-and-knowledge-work.md?raw';

// Strip the leading YAML frontmatter block and the first H1 (the page hero
// renders the title from metadata, so we don't want it twice in the body).
const bodyOf = (raw) =>
  String(raw || '')
    .replace(/^﻿?---\r?\n[\s\S]*?\r?\n---\r?\n/, '')
    .replace(/^\s*#\s+.*\r?\n/, '')
    .trim();

export const POSTS = [
  {
    slug: 'ai-native-coding-and-knowledge-work',
    title: 'Working AI-Native: A Field Guide to Coding and Knowledge Work in 2026',
    date: '2026-06-27',
    author: 'Taali',
    readingMinutes: 14,
    description:
      'The durable craft of working with AI — for engineers and everyone else. What the best practitioners actually do, what separates them from the people producing “workslop”, and the primary sources to learn it from.',
    body: bodyOf(aiNativeRaw),
  },
];

export const getPost = (slug) => POSTS.find((p) => p.slug === slug) || null;

export const formatPostDate = (iso) => {
  try {
    return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
  } catch {
    return iso;
  }
};
