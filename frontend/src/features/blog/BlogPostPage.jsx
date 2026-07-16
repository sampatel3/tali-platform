import React from 'react';
import { Link, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { MarketingNav } from '../../shared/layout/TaaliLayout';
import { useDocumentMeta } from '../../shared/seo/useDocumentMeta';
import { getPost, formatPostDate } from './posts';
import './blog.css';

// External links open safely in a new tab; in-app links stay SPA-routed.
const MdLink = ({ href = '', children }) => {
  const isExternal = /^https?:\/\//i.test(href);
  if (isExternal) {
    return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
  }
  return <Link to={href}>{children}</Link>;
};

const MD_COMPONENTS = { a: MdLink };

const BlogFooter = () => (
  <footer className="blog-footer">
    <div className="blog-container">
      © {new Date().getFullYear()} Taali ·{' '}
      <Link to="/blog">Blog</Link> ·{' '}
      <a href="https://www.taali.ai">taali.ai</a> ·{' '}
      <a href="mailto:hello@taali.ai">hello@taali.ai</a>
    </div>
  </footer>
);

export const BlogPostPage = ({ onNavigate }) => {
  const { slug } = useParams();
  const post = getPost(slug);
  useDocumentMeta(post ? {
    title: `${post.title} — Taali Blog`,
    description: post.description,
  } : undefined);

  return (
    <div className="blog-wrap">
      <MarketingNav onNavigate={onNavigate} />

      <main>
        {!post ? (
          <div className="blog-container" style={{ padding: '80px 20px' }}>
            <h1 style={{ fontFamily: 'var(--font-display)' }}>Post not found</h1>
            <p style={{ color: 'var(--ink-2)' }}>
              That post doesn’t exist. <Link to="/blog">Back to the blog →</Link>
            </p>
          </div>
        ) : (
          <article>
            <div className="blog-container">
              <header className="blog-hero">
                <Link to="/blog" className="blog-back">← Taali Blog</Link>
                <div className="blog-kicker">Field guide</div>
                <h1>{post.title}</h1>
                <p className="blog-dek">{post.description}</p>
                <div className="blog-meta">
                  <span>{post.author}</span>
                  <span>·</span>
                  <span>{formatPostDate(post.date)}</span>
                  <span>·</span>
                  <span>{post.readingMinutes} min read</span>
                </div>
              </header>

              <div className="blog-prose">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                  {post.body}
                </ReactMarkdown>
              </div>
            </div>
          </article>
        )}
      </main>
      <BlogFooter />
    </div>
  );
};

export default BlogPostPage;
