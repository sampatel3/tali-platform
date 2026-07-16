import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Repo paths, resolved from this file (frontend/src/test/seo.test.js).
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => fs.readFileSync(path.join(frontendRoot, rel), 'utf8');

const indexHtml = read('index.html');
const robots = read('public/robots.txt');
const sitemap = read('public/sitemap.xml');
const llms = read('public/llms.txt');
const investorDeck = read('public/_deck/index.html');
const routeShells = {
  developers: read('developers.html'),
  blog: read('blog.html'),
  terms: read('terms.html'),
  privacy: read('privacy.html'),
};

describe('index.html SEO/AEO head', () => {
  it('has a self-referential canonical to the www apex', () => {
    expect(indexHtml).toMatch(/<link rel="canonical" href="https:\/\/www\.taali\.ai\/"/);
  });

  it('declares an indexable robots policy', () => {
    expect(indexHtml).toMatch(/name="robots"[^>]*content="index, follow/);
  });

  it('has Open Graph + Twitter card tags pointing at the social image', () => {
    expect(indexHtml).toMatch(/property="og:title"/);
    expect(indexHtml).toMatch(/property="og:image" content="https:\/\/www\.taali\.ai\/og-image\.png(\?v=\d+)?"/);
    expect(indexHtml).toMatch(/name="twitter:card" content="summary_large_image"/);
  });

  it('ships a 1200x630 social image referenced by og:image', () => {
    expect(fs.existsSync(path.join(frontendRoot, 'public/og-image.png'))).toBe(true);
    expect(indexHtml).toMatch(/property="og:image:width" content="1200"/);
    expect(indexHtml).toMatch(/property="og:image:height" content="630"/);
  });
});

describe('route-specific crawlable shells', () => {
  for (const [slug, html] of Object.entries(routeShells)) {
    it(`serves ${slug} with its own canonical and description before JavaScript`, () => {
      expect(html).toContain(`https://www.taali.ai/${slug}`);
      expect(html).toMatch(/<meta name="description" content="[^"]+"/);
      expect(html).not.toContain('Agentic Hiring Platform & AI-Native Assessments');
      expect(html).toContain('src="/src/main.jsx"');
    });
  }

  it('serves the published article with article metadata before JavaScript', () => {
    const html = read('blog-ai-native.html');
    expect(html).toContain('https://www.taali.ai/blog/ai-native-coding-and-knowledge-work');
    expect(html).toContain('property="og:type" content="article"');
    expect(html).toContain('type="application/ld+json"');
    expect(html).toContain('src="/src/main.jsx"');
  });
});

describe('index.html structured data', () => {
  const blocks = [...indexHtml.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map(
    (m) => m[1],
  );

  it('embeds at least one valid JSON-LD block', () => {
    expect(blocks.length).toBeGreaterThan(0);
    for (const raw of blocks) {
      expect(() => JSON.parse(raw)).not.toThrow();
    }
  });

  it('describes the Organization, SoftwareApplication, and FAQ entities', () => {
    const types = blocks
      .flatMap((raw) => {
        const parsed = JSON.parse(raw);
        const nodes = parsed['@graph'] || [parsed];
        return nodes.map((n) => n['@type']);
      });
    expect(types).toContain('Organization');
    expect(types).toContain('SoftwareApplication');
    expect(types).toContain('FAQPage');
  });
});

describe('index.html crawlable content fallback', () => {
  // Non-JS AI crawlers only see the static markup, so the target topics must
  // be present in the raw HTML, not just rendered by React.
  it('mentions the three target topics in the static body', () => {
    expect(indexHtml.toLowerCase()).toContain('agentic hiring');
    expect(indexHtml.toLowerCase()).toContain('ai-native assessment');
    expect(indexHtml.toLowerCase()).toContain('ai-native hiring');
  });

  it('keeps the fallback inside #root so React replaces it on mount', () => {
    expect(indexHtml).toMatch(/<div id="root">[\s\S]*seo-fallback/);
  });
});

describe('robots.txt', () => {
  it('points crawlers at the sitemap', () => {
    expect(robots).toMatch(/Sitemap:\s*https:\/\/www\.taali\.ai\/sitemap\.xml/);
  });

  it('explicitly welcomes the major AI crawlers', () => {
    for (const bot of ['GPTBot', 'ClaudeBot', 'PerplexityBot', 'OAI-SearchBot', 'Google-Extended']) {
      expect(robots).toContain(`User-agent: ${bot}`);
    }
  });

  it('keeps crawlers off candidate share links (PII) and the signed-in app', () => {
    expect(robots).toMatch(/Disallow:\s*\/share\//);
    expect(robots).toMatch(/Disallow:\s*\/settings/);
  });
});

describe('sitemap.xml', () => {
  it('is a urlset listing the public marketing pages', () => {
    expect(sitemap).toMatch(/<urlset[^>]*sitemaps\.org\/schemas\/sitemap\/0\.9/);
    expect(sitemap).toContain('<loc>https://www.taali.ai/</loc>');
    expect(sitemap).toContain('<loc>https://www.taali.ai/demo</loc>');
  });
});

describe('llms.txt', () => {
  it('opens with the Taali H1 and a summary blockquote', () => {
    expect(llms).toMatch(/^#\s+Taali/m);
    expect(llms).toMatch(/^>\s+/m);
  });
});

// --- Keyword content pages (static, crawlable guide pages) ---

const repoRoot = path.resolve(frontendRoot, '..');
const rootVercel = JSON.parse(fs.readFileSync(path.join(repoRoot, 'vercel.json'), 'utf8'));
const contentCss = read('public/styles/content.css');

const CONTENT_PAGES = [
  { file: 'public/agentic-hiring.html', slug: '/agentic-hiring', topic: 'agentic hiring' },
  { file: 'public/ai-native-hiring.html', slug: '/ai-native-hiring', topic: 'ai-native hiring' },
  { file: 'public/ai-native-assessments.html', slug: '/ai-native-assessments', topic: 'ai-native assessment' },
];

describe('keyword content pages', () => {
  for (const page of CONTENT_PAGES) {
    describe(page.file, () => {
      const html = read(page.file);

      it('has a self-referential canonical to its clean URL', () => {
        expect(html).toContain(`<link rel="canonical" href="https://www.taali.ai${page.slug}" />`);
      });

      it('is indexable', () => {
        expect(html).toMatch(/name="robots"[^>]*content="index, follow/);
      });

      it('embeds valid Article + FAQ + breadcrumb structured data', () => {
        const m = html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/);
        expect(m).not.toBeNull();
        const data = JSON.parse(m[1]);
        const types = (data['@graph'] || [data]).map((n) => n['@type']);
        expect(types).toContain('Article');
        expect(types).toContain('FAQPage');
        expect(types).toContain('BreadcrumbList');
      });

      it('covers its target topic in the body', () => {
        expect(html.toLowerCase()).toContain(page.topic);
      });

      it('uses the shared content stylesheet and links to home + the other guides', () => {
        expect(html).toContain('/styles/content.css');
        expect(html).toContain('href="/"');
        for (const other of CONTENT_PAGES.filter((p) => p !== page)) {
          expect(html).toContain(`href="${other.slug}"`);
        }
      });

      it('uses the real Taali design chrome (app-nav, logo mark, primary button)', () => {
        expect(html).toContain('class="app-nav"');
        expect(html).toContain('class="logo"');
        expect(html).toContain('btn-primary');
        // The real TaaliTile logo mark path.
        expect(html).toContain('M6 4.5v15M10 4.5v15M14 4.5v15M18 4.5v15M4 18.5L20 5.5');
      });

      it('surfaces a product snapshot so the guide shows the product', () => {
        expect(html).toContain('class="product"');
      });
    });
  }

  it('ships the shared content stylesheet built on the real design tokens', () => {
    expect(contentCss).toContain('.app-nav');
    expect(contentCss).toContain('.btn-primary');
    expect(contentCss).toContain('--purple');
    expect(contentCss).toContain('data-theme="dark"');
    // Matches the app's 80% density (root 0.8rem) so the guides don't look zoomed.
    expect(contentCss).toContain('font-size: 0.8rem');
    // The static product-UI kit (KPIs, score chips, five-axis report).
    expect(contentCss).toContain('.score-chip');
    expect(contentCss).toContain('.axis');
  });
});

describe('vercel rewrites', () => {
  it('serves each clean URL from its static file before the SPA catch-all', () => {
    const sources = rootVercel.rewrites.map((r) => r.source);
    const catchAllIdx = sources.indexOf('/(.*)');
    expect(catchAllIdx).toBeGreaterThanOrEqual(0);
    for (const slug of ['/agentic-hiring', '/ai-native-hiring', '/ai-native-assessments', '/developers', '/blog', '/terms', '/privacy']) {
      const idx = sources.indexOf(slug);
      expect(idx).toBeGreaterThanOrEqual(0);
      expect(idx).toBeLessThan(catchAllIdx);
      expect(rootVercel.rewrites.find((r) => r.source === slug).destination).toBe(`${slug}.html`);
    }
    const article = rootVercel.rewrites.find((r) => r.source === '/blog/ai-native-coding-and-knowledge-work');
    expect(article.destination).toBe('/blog-ai-native.html');
  });
});

describe('sitemap + internal linking', () => {
  it('lists the three guide pages', () => {
    for (const slug of ['agentic-hiring', 'ai-native-hiring', 'ai-native-assessments']) {
      expect(sitemap).toContain(`<loc>https://www.taali.ai/${slug}</loc>`);
    }
  });

  it('lists the developer portal and legal notices', () => {
    for (const slug of ['developers', 'terms', 'privacy']) {
      expect(sitemap).toContain(`<loc>https://www.taali.ai/${slug}</loc>`);
    }
  });

  it('lists the crawlable blog index and published article', () => {
    expect(sitemap).toContain('<loc>https://www.taali.ai/blog</loc>');
    expect(sitemap).toContain('<loc>https://www.taali.ai/blog/ai-native-coding-and-knowledge-work</loc>');
  });

  it('links the guides from the home-page crawlable fallback', () => {
    for (const slug of ['/agentic-hiring', '/ai-native-hiring', '/ai-native-assessments']) {
      expect(indexHtml).toContain(`href="${slug}"`);
    }
  });
});

describe('public investor deck routes', () => {
  it('embeds the public Jobs showcase rather than the authenticated recruiter route', () => {
    expect(investorDeck).toContain('iframe src="/showcase/jobs"');
    expect(investorDeck).not.toContain('iframe src="/jobs?demo=1');
  });
});
