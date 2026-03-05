import React from 'react';
import {
  Legend,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from 'recharts';

import {
  COMPARISON_CATEGORY_CONFIG,
  COMPARISON_CATEGORY_KEYS,
  getCategoryScoresFromAssessment,
} from '../../lib/comparisonCategories';

const DEFAULT_COLORS = ['var(--taali-text)', 'var(--taali-success)', 'var(--taali-warning)', 'var(--taali-info)', '#ef4444'];

export const ComparisonRadar = ({
  assessments = [],
  highlightAssessmentId = null,
  className = '',
  height = 340,
  showLegend = true,
  categoryKeys = null,
}) => {
  if (!Array.isArray(assessments) || assessments.length === 0) {
    return <div className="text-sm text-[var(--taali-muted)]">No comparison data selected.</div>;
  }

  const categoryConfigByKey = COMPARISON_CATEGORY_CONFIG.reduce((acc, item) => {
    acc[item.key] = item;
    return acc;
  }, {});
  const selectedCategoryConfig = (
    Array.isArray(categoryKeys)
      ? categoryKeys.filter((key) => COMPARISON_CATEGORY_KEYS.includes(key))
      : COMPARISON_CATEGORY_KEYS
  )
    .map((key) => categoryConfigByKey[key])
    .filter(Boolean);
  const effectiveCategoryConfig = selectedCategoryConfig.length
    ? selectedCategoryConfig
    : COMPARISON_CATEGORY_CONFIG;

  const keyFor = (index) => `_series_${index}`;
  const radarData = effectiveCategoryConfig.map((category) => {
    const row = { dimension: category.label, fullMark: 10 };
    assessments.forEach((assessment, index) => {
      const scores = getCategoryScoresFromAssessment(assessment);
      row[keyFor(index)] = scores[category.key] ?? 0;
    });
    return row;
  });

  return (
    <div className={className}>
      <div className="w-full" style={{ height }}>
        <ResponsiveContainer>
          <RadarChart data={radarData}>
            <PolarGrid stroke="var(--taali-border-muted)" />
            <PolarAngleAxis
              dataKey="dimension"
              tick={{ fontSize: 10, fontFamily: 'var(--taali-font)', fill: 'var(--taali-muted)' }}
            />
            <PolarRadiusAxis
              domain={[0, 10]}
              tick={{ fontSize: 10, fill: 'var(--taali-muted)' }}
              axisLine={{ stroke: 'var(--taali-border-soft)' }}
            />
            {assessments.map((assessment, index) => {
              const isHighlight = highlightAssessmentId != null && Number(assessment.id) === Number(highlightAssessmentId);
              const color = isHighlight ? 'var(--taali-purple)' : DEFAULT_COLORS[index % DEFAULT_COLORS.length];
              return (
                <Radar
                  key={`${assessment.id}-${index}`}
                  name={assessment.name || `Candidate ${index + 1}`}
                  dataKey={keyFor(index)}
                  stroke={color}
                  fill={color}
                  fillOpacity={isHighlight ? 0.25 : 0.12}
                  strokeWidth={isHighlight ? 2 : 1.5}
                />
              );
            })}
            {showLegend ? <Legend wrapperStyle={{ color: 'var(--taali-text)', fontSize: 12 }} /> : null}
          </RadarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
