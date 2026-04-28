import React, { useEffect, useState } from 'react';
import { Sparkles, X, Network, List as ListIcon } from 'lucide-react';

/**
 * Natural-language search bar with parsed-filter chips and a List/Graph
 * view toggle. Stateless about whether NL search is active — that lives
 * in the parent page.
 *
 * Props:
 *   nlQuery:        the active query string (or '')
 *   onSubmit(q):    fired when the recruiter submits a new query
 *   onClear():      fired when the recruiter clicks the global clear-X
 *   parsedFilter:   the server-echoed parsed_filter object (or null)
 *   onRemoveChip(chipKey, chipValue): fired to drop a single chip
 *   warnings:       list of {code, message} surfaced beneath the input
 *   viewMode:       'list' | 'graph'
 *   onViewModeChange(next):  toggle handler
 *   isLoading:      shows a "thinking..." subtle indicator
 */
const EXAMPLE_QUERIES = [
  'AWS Glue experience, based in UK',
  '5+ years, worked in Europe, large enterprise',
  'Worked at Google or Meta in last 3 years',
  'Python and Kubernetes, in production',
];

export function NLSearchBar({
  nlQuery,
  onSubmit,
  onClear,
  parsedFilter,
  onRemoveChip,
  warnings = [],
  viewMode = 'list',
  onViewModeChange,
  isLoading = false,
}) {
  const [draft, setDraft] = useState(nlQuery || '');
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    setDraft(nlQuery || '');
  }, [nlQuery]);

  const submit = (event) => {
    event.preventDefault();
    const cleaned = (draft || '').trim();
    if (!cleaned) return;
    onSubmit(cleaned);
  };

  const chips = buildChips(parsedFilter);
  const hasChips = chips.length > 0;
  const showSuggestions = focused && !nlQuery && !draft.trim();

  return (
    <div className="nl-search">
      <form onSubmit={submit} className="nl-search__form">
        <Sparkles size={16} className="nl-search__icon" aria-hidden />
        <input
          className="nl-search__input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 150)}
          placeholder="Ask anything — e.g. 'AWS Glue experience, worked in Europe, 5+ years'"
          aria-label="Natural-language candidate search"
        />
        {nlQuery ? (
          <button
            type="button"
            className="nl-search__clear"
            onClick={() => {
              setDraft('');
              onClear();
            }}
            aria-label="Clear natural-language query"
          >
            <X size={14} />
          </button>
        ) : null}
        <button type="submit" className="btn btn-purple btn-sm nl-search__submit">
          {isLoading ? 'Thinking…' : 'Search'}
        </button>
        <div className="segset" role="group" aria-label="View mode">
          <button
            type="button"
            className={viewMode === 'list' ? 'on' : ''}
            onClick={() => onViewModeChange('list')}
            aria-pressed={viewMode === 'list'}
          >
            <ListIcon size={14} aria-hidden /> List
          </button>
          <button
            type="button"
            className={viewMode === 'graph' ? 'on' : ''}
            onClick={() => onViewModeChange('graph')}
            aria-pressed={viewMode === 'graph'}
          >
            <Network size={14} aria-hidden /> Graph
          </button>
        </div>
      </form>

      {showSuggestions ? (
        <div className="nl-search__suggestions" role="listbox">
          <div className="nl-search__suggestions-label">Try:</div>
          {EXAMPLE_QUERIES.map((example) => (
            <button
              key={example}
              type="button"
              className="nl-search__suggestion"
              onMouseDown={(event) => {
                event.preventDefault();
                setDraft(example);
                onSubmit(example);
              }}
            >
              {example}
            </button>
          ))}
        </div>
      ) : null}

      {hasChips ? (
        <div className="nl-search__chips" aria-label="Parsed search criteria">
          {chips.map((chip) => (
            <span key={chip.key} className="nl-search__chip">
              <span className="nl-search__chip-label">{chip.label}</span>
              <button
                type="button"
                onClick={() => onRemoveChip(chip.field, chip.value)}
                aria-label={`Remove ${chip.label}`}
              >
                <X size={12} />
              </button>
            </span>
          ))}
          <button type="button" className="nl-search__chip-clear" onClick={onClear}>
            Clear all
          </button>
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <ul className="nl-search__warnings">
          {warnings.map((w) => (
            <li key={`${w.code}-${w.message}`} className={`nl-search__warning nl-search__warning--${w.code}`}>
              {w.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function buildChips(parsedFilter) {
  if (!parsedFilter) return [];
  const out = [];
  (parsedFilter.skills_all || []).forEach((s) => {
    out.push({ key: `skills_all:${s}`, field: 'skills_all', value: s, label: `Skill: ${s}` });
  });
  (parsedFilter.skills_any || []).forEach((s) => {
    out.push({ key: `skills_any:${s}`, field: 'skills_any', value: s, label: `Skill (any): ${s}` });
  });
  (parsedFilter.locations_country || []).forEach((c) => {
    out.push({ key: `country:${c}`, field: 'locations_country', value: c, label: `Country: ${c}` });
  });
  (parsedFilter.locations_region || []).forEach((r) => {
    const display = r.replace(/\b\w/g, (m) => m.toUpperCase());
    out.push({ key: `region:${r}`, field: 'locations_region', value: r, label: `Region: ${display}` });
  });
  if (typeof parsedFilter.min_years_experience === 'number' && parsedFilter.min_years_experience > 0) {
    out.push({
      key: 'years',
      field: 'min_years_experience',
      value: parsedFilter.min_years_experience,
      label: `${parsedFilter.min_years_experience}+ years`,
    });
  }
  (parsedFilter.graph_predicates || []).forEach((p, idx) => {
    const verb = p.type === 'worked_at' ? 'Worked at' : p.type === 'studied_at' ? 'Studied at' : p.type;
    out.push({
      key: `graph:${idx}:${p.type}:${p.value}`,
      field: 'graph_predicates',
      value: p,
      label: `${verb}: ${p.value}`,
    });
  });
  (parsedFilter.soft_criteria || []).forEach((c) => {
    out.push({ key: `soft:${c}`, field: 'soft_criteria', value: c, label: c });
  });
  (parsedFilter.keywords || []).forEach((k) => {
    out.push({ key: `kw:${k}`, field: 'keywords', value: k, label: `Keyword: ${k}` });
  });
  return out;
}
