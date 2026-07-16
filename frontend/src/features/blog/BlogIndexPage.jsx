import React from 'react';
import { Link } from 'react-router-dom';

import { MarketingNav } from '../../shared/layout/TaaliLayout';
import { POSTS, formatPostDate } from './posts';
import './blog.css';

// Evergreen SEO guides — static HTML in frontend/public/*.html, served via
// Vercel rewrites. Surfaced here so /blog carries both posts AND guides on one
// page (the landing footer no longer has a separate Guides column).
const GUIDES = [
  {
    href: '/agentic-hiring',
    title: 'What is agentic hiring?',
    description: 'How an AI agent ingests applicants, screens, assesses, and advances the routine funnel — while humans retain irreversible and ambiguous calls.',
  },
  {
    href: '/ai-native-hiring',
    title: 'AI-native hiring',
    description: 'What changes when hiring is built around an agent from the ground up, not bolted onto a legacy ATS.',
  },
  {
    href: '/ai-native-assessments',
    title: 'AI-native assessments',
    description: 'Measuring how people actually work with AI — the five dimensions scored from a real working session.',
  },
];

const BlogFooter = () => (
  <footer className="blog-footer">
    <div className="blog-container">
      © {new Date().getFullYear()} Taali ·{' '}
      <a href="https://www.taali.ai">taali.ai</a> ·{' '}
      <a href="mailto:hello@taali.ai">hello@taali.ai</a>
    </div>
  </footer>
);

export const BlogIndexPage = ({ onNavigate }) => (
  <div className="blog-wrap">
    <MarketingNav onNavigate={onNavigate} />

    <main className="blog-container">
      <header className="blog-index-head blog-hero">
        <div className="blog-kicker">Taali Blog</div>
        <h1>Writing on AI-native work</h1>
        <p className="blog-dek">
          How the best people actually work with AI — for engineering and beyond — and how we measure it.
        </p>
      </header>

      {POSTS.map((post) => (
        <Link key={post.slug} to={`/blog/${post.slug}`} className="blog-card">
          <div className="blog-meta" style={{ marginTop: 0 }}>
            <span>{formatPostDate(post.date)}</span>
            <span>·</span>
            <span>{post.readingMinutes} min read</span>
          </div>
          <h2>{post.title}</h2>
          <p>{post.description}</p>
        </Link>
      ))}

      <section className="blog-guides">
        <h2 className="blog-guides-title">Guides</h2>
        {GUIDES.map((guide) => (
          <a key={guide.href} href={guide.href} className="blog-card">
            <h2>{guide.title}</h2>
            <p>{guide.description}</p>
          </a>
        ))}
      </section>
    </main>

    <BlogFooter />
  </div>
);

export default BlogIndexPage;
