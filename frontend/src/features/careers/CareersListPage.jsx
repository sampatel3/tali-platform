import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { MapPin, Briefcase } from 'lucide-react';

import {
  Badge,
  Card,
  EmptyState,
  PageContainer,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import { careersApi } from './api';

const locationLabel = (job) =>
  [job.location_city, job.location_country].filter(Boolean).join(', ');

// Public, no-auth careers landing for one organisation: /careers/:orgSlug.
export const CareersListPage = () => {
  const { orgSlug } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    careersApi
      .listJobs(orgSlug)
      .then((res) => { if (!cancelled) { setData(res); setLoading(false); } })
      .catch((err) => {
        if (!cancelled) {
          setError(err?.response?.status === 404 ? 'Organisation not found.' : 'Failed to load openings.');
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [orgSlug]);

  if (loading) {
    return <div className="flex min-h-screen items-center justify-center"><Spinner /></div>;
  }
  if (error) {
    return (
      <PageContainer>
        <EmptyState title="Careers" description={error} />
      </PageContainer>
    );
  }

  const jobs = data?.jobs || [];
  return (
    <PageContainer width="default">
      <header className="mb-8">
        <p className="text-xs font-medium uppercase tracking-wide text-[var(--taali-muted)]">Careers</p>
        <h1 className="mt-1 text-2xl font-semibold text-[var(--taali-text)]">
          Open roles at {data?.organization}
        </h1>
      </header>

      {jobs.length === 0 ? (
        <EmptyState title="No open roles" description="There are no published openings right now. Check back soon." />
      ) : (
        <div className="space-y-3">
          {jobs.map((job) => (
            <Card
              key={job.slug}
              as="button"
              className="flex w-full items-start justify-between gap-4 px-4 py-4 text-left transition hover:border-[var(--taali-purple)]"
              onClick={() => navigate(`/careers/${encodeURIComponent(orgSlug)}/${encodeURIComponent(job.slug)}`)}
            >
              <div className="min-w-0">
                <div className="truncate text-base font-semibold text-[var(--taali-text)]">{job.title}</div>
                <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-[var(--taali-muted)]">
                  {job.department ? (
                    <span className="inline-flex items-center gap-1"><Briefcase size={13} />{job.department}</span>
                  ) : null}
                  {locationLabel(job) ? (
                    <span className="inline-flex items-center gap-1"><MapPin size={13} />{locationLabel(job)}</span>
                  ) : null}
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
                {job.employment_type ? <Badge variant="muted">{job.employment_type}</Badge> : null}
                {job.workplace_type ? <Badge variant="info">{job.workplace_type}</Badge> : null}
              </div>
            </Card>
          ))}
        </div>
      )}
    </PageContainer>
  );
};

export default CareersListPage;
