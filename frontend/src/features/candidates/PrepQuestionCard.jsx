// PrepQuestionCard — the interview-prep question card for the candidate
// standing report's "Interview prep" tab. Renders the canvas cand-prep
// layout (mono kicker + question + LISTEN FOR / CONCERNING IF columns +
// evidence anchor). Extracted verbatim from CandidateStandingReportPage.jsx
// to keep the page file under the frontend architecture line cap.
import React from 'react';

import { asCleanText } from './candidatesUiUtils';

const toBulletList = (value) => {
  if (Array.isArray(value)) return value.filter(Boolean).map(asCleanText).filter(Boolean);
  const text = asCleanText(value);
  return text ? [text] : [];
};

// PrepQuestionCard — canvas cand-prep card layout:
//   QUESTION NN · {source}    (mono purple kicker)
//   {question}                 (display, weight 500)
//   LISTEN FOR (green mono)    |  CONCERNING IF (red mono)
//   {listenFor bullets}        |  {concern bullets}
const PrepQuestionCard = ({ item, number, listenLabel, concernLabel, fallbackConcern }) => {
  const listenItems = toBulletList(item?.listenFor);
  const concernItems = toBulletList(item?.redFlags || item?.followUp);
  const evidenceText = asCleanText(item?.evidence);
  const contextText = asCleanText(item?.context);
  return (
    <div className="mc-prep-card">
      <div className="mc-prep-card-kicker">
        QUESTION {String(number).padStart(2, '0')} · {item?.source || 'Standing report'}
      </div>
      <div className="mc-prep-card-question">{item?.question}</div>
      {contextText ? (
        <div className="mc-prep-card-context">{contextText}</div>
      ) : null}
      <div className="mc-prep-card-grid">
        <div>
          <div className="mc-prep-card-label is-listen">{listenLabel}</div>
          <ul className="mc-prep-card-list">
            {(listenItems.length ? listenItems : ['Specific examples tied to the candidate evidence.']).map((line, idx) => (
              <li key={`listen-${idx}`}>{line}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="mc-prep-card-label is-concern">{concernLabel}</div>
          <ul className="mc-prep-card-list">
            {(concernItems.length ? concernItems : [fallbackConcern]).map((line, idx) => (
              <li key={`concern-${idx}`}>{line}</li>
            ))}
          </ul>
        </div>
      </div>
      {evidenceText ? (
        <div className="mc-prep-card-evidence">
          <div className="mc-prep-card-evidence-label">ANCHOR IN</div>
          <div>{evidenceText}</div>
        </div>
      ) : null}
    </div>
  );
};

export { PrepQuestionCard };
