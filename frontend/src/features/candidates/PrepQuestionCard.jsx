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

// PrepQuestionCard — report-preview's `.prep` card layout:
//   QUESTION NN · {source}              (mono purple kicker)
//   {question}                           (display, weight 500)
//   Listen for     | {listen cues}       (single-column `.cue` rows: a
//   Concerning if  | {concern cues}        mono mute label column + value;
//   Anchor in      | {evidence}            in-scheme, NOT green/red)
const PrepQuestionCard = ({ item, number, listenLabel, concernLabel, fallbackConcern }) => {
  const listenItems = toBulletList(item?.listenFor);
  const concernItems = toBulletList(item?.redFlags || item?.followUp);
  const evidenceText = asCleanText(item?.evidence);
  const contextText = asCleanText(item?.context);
  const listenText = (listenItems.length ? listenItems : ['Specific examples tied to the candidate evidence.']).join(' ');
  const concernText = (concernItems.length ? concernItems : [fallbackConcern].filter(Boolean)).join(' ');
  return (
    <div className="mc-prep-card">
      <div className="mc-prep-card-kicker">
        QUESTION {String(number).padStart(2, '0')} · {item?.source || 'Standing report'}
      </div>
      <div className="mc-prep-card-question">{item?.question}</div>
      {contextText ? (
        <div className="mc-prep-card-context">{contextText}</div>
      ) : null}
      <div className="mc-prep-cue">
        <span className="mc-prep-cue-lab">{listenLabel}</span>
        <span>{listenText}</span>
      </div>
      {concernText ? (
        <div className="mc-prep-cue">
          <span className="mc-prep-cue-lab">{concernLabel}</span>
          <span>{concernText}</span>
        </div>
      ) : null}
      {evidenceText ? (
        <div className="mc-prep-cue">
          <span className="mc-prep-cue-lab">Anchor in</span>
          <span>{evidenceText}</span>
        </div>
      ) : null}
    </div>
  );
};

export { PrepQuestionCard };
