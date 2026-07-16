#!/usr/bin/env node

/**
 * Chat-system architecture guard.
 *
 * This intentionally protects boundaries instead of snapshotting markup. The
 * chat redesign is being migrated incrementally, so the guard allows the small
 * set of documented legacy cross-feature imports and CSS overrides that exist
 * today while preventing new copies from spreading. See CHAT_DESIGN_SYSTEM.md.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, '..');
const sourceRoot = path.join(projectRoot, 'src');
const sharedChatRoot = path.join(sourceRoot, 'shared', 'chat');
const searchChatRoot = path.join(sourceRoot, 'features', 'chat');
const homeAgentChatRoot = path.join(sourceRoot, 'features', 'home', 'agentchat');
const sourceExtensions = new Set(['.css', '.js', '.jsx', '.ts', '.tsx']);
const componentExtensions = new Set(['.js', '.jsx', '.ts', '.tsx']);
const violations = [];

const toRelative = (fullPath) => path.relative(projectRoot, fullPath).split(path.sep).join('/');
const isWithin = (fullPath, root) => fullPath === root || fullPath.startsWith(`${root}${path.sep}`);
const lineFor = (content, index) => content.slice(0, index).split('\n').length;

const walk = (dir, files = []) => {
  if (!fs.existsSync(dir)) return files;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === 'dist') continue;
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(fullPath, files);
    else if (sourceExtensions.has(path.extname(entry.name))) files.push(fullPath);
  }
  return files;
};

const requiredSharedFiles = [
  'AgentPromptCard.jsx',
  'ChatActivity.css',
  'ChatActivity.jsx',
  'ChatArtifact.css',
  'ChatArtifact.jsx',
  'ChatArtifacts.css',
  'ChatComposer.jsx',
  'ChatEmptyState.jsx',
  'ChatMarkdown.jsx',
  'ChatMessage.jsx',
  'ChatSurface.jsx',
  'NewMessageNotice.jsx',
  'RoleAgentTimeline.jsx',
  'ThinkingDots.jsx',
  'chat-kit.css',
  'index.js',
  'useAgentRequestReply.js',
  'useAgentUpdateAwareness.js',
];

for (const fileName of requiredSharedFiles) {
  const fullPath = path.join(sharedChatRoot, fileName);
  if (!fs.existsSync(fullPath)) {
    violations.push(`src/shared/chat/${fileName} is required by the shared chat contract.`);
  }
}

const barrelPath = path.join(sharedChatRoot, 'index.js');
if (fs.existsSync(barrelPath)) {
  const barrel = fs.readFileSync(barrelPath, 'utf8');
  const requiredExports = [
    'AgentHelperPromptCard',
    'AgentPromptCard',
    'ChatActivity',
    'ChatArtifact',
    'ChatComposer',
    'ChatEmptyState',
    'ChatMarkdown',
    'ChatMessage',
    'ChatSurface',
    'NewMessageNotice',
    'RoleAgentTimeline',
    'ThinkingDots',
    'useAgentRequestReply',
    'useAgentUpdateAwareness',
  ];
  if (!/import\s+['"]\.\/chat-kit\.css['"]/.test(barrel)) {
    violations.push('src/shared/chat/index.js must load the shared chat stylesheet.');
  }
  if (/ChatArtifacts\.css/.test(barrel)) {
    violations.push('src/shared/chat/index.js may not globally inject optional artifact styles.');
  }
  for (const exportName of requiredExports) {
    if (!new RegExp(`\\b${exportName}\\b`).test(barrel)) {
      violations.push(`src/shared/chat/index.js must export ${exportName}.`);
    }
  }
}

const artifactOwnerPath = path.join(homeAgentChatRoot, 'cards.jsx');
if (fs.existsSync(artifactOwnerPath)) {
  const artifactOwner = fs.readFileSync(artifactOwnerPath, 'utf8');
  if (!/import\s+['"][^'"]*shared\/chat\/ChatArtifacts\.css['"]/.test(artifactOwner)) {
    violations.push('src/features/home/agentchat/cards.jsx must directly load its cold-route-safe artifact styles.');
  }
}

// A primitive with one of these public names must have exactly one owner. A
// feature may compose it, but may not quietly create another implementation.
const canonicalPrimitivePattern = /\b(?:export\s+)?(?:const|function)\s+(AgentHelperPromptCard|AgentPromptCard|ChatActivity|ChatArtifact|ChatComposer|ChatEmptyState|ChatMarkdown|ChatMessage|ChatSurface|NewMessageNotice|RoleAgentTimeline|ThinkingDots|useAgentRequestReply|useAgentUpdateAwareness)\b/g;
const directPrimitiveImportPattern = /from\s+['"][^'"]*shared\/chat\/(AgentPromptCard|ChatActivity|ChatArtifact|ChatComposer|ChatEmptyState|ChatMarkdown|ChatMessage|ChatSurface|NewMessageNotice|RoleAgentTimeline|ThinkingDots|useAgentRequestReply|useAgentUpdateAwareness)(?:\.[jt]sx?)?['"]/g;
const featureImportPattern = /(?:from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\))/g;

// These are the known migration seams. They are listed explicitly so a third
// cross-feature dependency cannot be added by accident. Removing an entry from
// production code is always safe; delete its allowlist row in the same change.
const allowedCrossFeatureEdges = new Set([
  'src/features/chat/AgentConversation.jsx -> ../home/agentchat/cards.jsx',
  'src/features/home/agentchat/AgentChatDock.jsx -> ../../chat/CandidateEvidenceCard',
]);

// A few surfaces still apply density-only overrides to shared tk-* primitives.
// New surfaces must use explicit shared density/variant props instead of
// reaching into another component's implementation classes.
const allowedSharedClassOverrideFiles = new Set([
  'src/features/chat/chat.css',
  'src/features/home/agentchat/agentchat.css',
  'src/features/requisitions/requisitions.css',
  'src/styles/26-buttons.css',
]);
const sharedClassSelectorPattern = /\.tk-[\w-]+/;
const featureOwnedSelectorPattern = /\.(?:ac|cp)-[\w-]+/;

// Inspect only the header immediately before each CSS block. This avoids
// mistaking token usages in declarations for selectors and, unlike a broad
// multiline regex, stays linear on large generated-looking stylesheets.
const selectorOffsets = (content, selectorPattern) => {
  const offsets = [];
  const scannable = content.replace(/\/\*[\s\S]*?\*\//g, (comment) => (
    comment.replace(/[^\n]/g, ' ')
  ));
  let boundary = -1;
  for (let index = 0; index < scannable.length; index += 1) {
    const character = scannable[index];
    if (character === '{') {
      const header = scannable.slice(boundary + 1, index);
      const match = selectorPattern.exec(header);
      if (match) offsets.push(boundary + 1 + match.index);
      boundary = index;
    } else if (character === '}') {
      boundary = index;
    }
  }
  return offsets;
};

// The developer gallery documents legacy aliases; production chat must use
// shared/ui Button so geometry, focus, loading, and motion stay consistent.
const allowedLegacyAgentButtonFiles = new Set([
  'src/features/dev/ButtonShowcasePage.jsx',
]);
const legacyAgentButtonPattern = /className\s*=\s*(?:['"][^'"]*\bac-btn\b|\{`[^`]*\bac-btn\b)/g;

const allowedLegacyChatButtonFiles = new Set([
  'src/features/dev/ButtonShowcasePage.jsx',
]);
const legacyChatButtonPattern = /className\s*=\s*(?:['"][^'"]*\bcp-btn-(?:ghost|primary|danger)\b|\{`[^`]*\bcp-btn-(?:ghost|primary|danger)\b)/g;

for (const fullPath of walk(sourceRoot)) {
  const relativePath = toRelative(fullPath);
  const content = fs.readFileSync(fullPath, 'utf8');
  const extension = path.extname(fullPath);

  if (componentExtensions.has(extension)) {
    for (const match of content.matchAll(canonicalPrimitivePattern)) {
      if (!isWithin(fullPath, sharedChatRoot)) {
        violations.push(
          `${relativePath}:${lineFor(content, match.index)} — ${match[1]} belongs in src/shared/chat; compose or extend the shared primitive instead.`,
        );
      }
    }

    for (const match of content.matchAll(directPrimitiveImportPattern)) {
      if (!isWithin(fullPath, sharedChatRoot)) {
        violations.push(
          `${relativePath}:${lineFor(content, match.index)} — import ${match[1]} from the src/shared/chat barrel.`,
        );
      }
    }

    if (isWithin(fullPath, sharedChatRoot)) {
      for (const match of content.matchAll(featureImportPattern)) {
        const specifier = match[1] || match[2] || match[3];
        if (!specifier?.startsWith('.')) continue;
        const resolved = path.resolve(path.dirname(fullPath), specifier);
        if (isWithin(resolved, path.join(sourceRoot, 'features'))) {
          violations.push(
            `${relativePath}:${lineFor(content, match.index)} — shared chat primitives may not import feature code.`,
          );
        }
      }
    }

    for (const match of content.matchAll(featureImportPattern)) {
      const specifier = match[1] || match[2] || match[3];
      if (!specifier?.startsWith('.')) continue;
      const resolved = path.resolve(path.dirname(fullPath), specifier);
      const crossesChatFeatures = (
        isWithin(fullPath, searchChatRoot) && isWithin(resolved, homeAgentChatRoot)
      ) || (
        isWithin(fullPath, homeAgentChatRoot) && isWithin(resolved, searchChatRoot)
      );
      if (!crossesChatFeatures) continue;
      const edge = `${relativePath} -> ${specifier}`;
      if (!allowedCrossFeatureEdges.has(edge)) {
        violations.push(
          `${relativePath}:${lineFor(content, match.index)} — new Search/Home chat cross-import; move the shared capability to src/shared/chat.`,
        );
      }
    }

    if (!allowedLegacyAgentButtonFiles.has(relativePath)) {
      for (const match of content.matchAll(legacyAgentButtonPattern)) {
        violations.push(
          `${relativePath}:${lineFor(content, match.index)} — new ac-btn usage; use shared/ui Button.`,
        );
      }
    }
    if (!allowedLegacyChatButtonFiles.has(relativePath)) {
      for (const match of content.matchAll(legacyChatButtonPattern)) {
        violations.push(
          `${relativePath}:${lineFor(content, match.index)} — new cp-btn-* usage; use shared/ui Button.`,
        );
      }
    }
  }

  if (extension === '.css' && isWithin(fullPath, sharedChatRoot)) {
    for (const offset of selectorOffsets(content, featureOwnedSelectorPattern)) {
      violations.push(
        `${relativePath}:${lineFor(content, offset)} — shared chat CSS may not target Search/Home feature classes.`,
      );
    }
  } else if (extension === '.css' && !allowedSharedClassOverrideFiles.has(relativePath)) {
    for (const offset of selectorOffsets(content, sharedClassSelectorPattern)) {
      violations.push(
        `${relativePath}:${lineFor(content, offset)} — new tk-* implementation override; add an explicit shared chat variant instead.`,
      );
    }
  }
}

if (violations.length > 0) {
  console.error('Chat-system architecture guard failed:');
  for (const violation of violations) console.error(`- ${violation}`);
  process.exit(1);
}

console.log('Chat-system architecture guard passed.');
