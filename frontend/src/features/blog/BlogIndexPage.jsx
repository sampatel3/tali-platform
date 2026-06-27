import React from 'react';
import { Link } from 'react-router-dom';

import { MarketingNav } from '../../shared/layout/TaaliLayout';
import { POSTS, formatPostDate } from './posts';
import './blog.css';

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

    <div className="blog-container">
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
    </div>

    <BlogFooter />
  </div>
);

export default BlogIndexPage;
