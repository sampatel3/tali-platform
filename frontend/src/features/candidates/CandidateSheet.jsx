import React, { useEffect, useState } from 'react';
import { UploadCloud } from 'lucide-react';

import {
  Button,
  Card,
  Input,
  Sheet,
  cx,
} from '../../shared/ui/TaaliPrimitives';

export const CandidateSheet = ({
  open,
  role,
  saving,
  error,
  onClose,
  onSubmit,
}) => {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [position, setPosition] = useState('');
  const [cvFile, setCvFile] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  const [touched, setTouched] = useState({
    email: false,
    name: false,
    cv: false,
  });

  useEffect(() => {
    if (!open) return;
    setEmail('');
    setName('');
    setPosition(role?.name || '');
    setCvFile(null);
    setDragActive(false);
    setTouched({ email: false, name: false, cv: false });
  }, [open, role]);

  const hasRoleSpec = Boolean(role?.job_spec_filename);
  const validEmail = email.trim().length > 0;
  const validName = name.trim().length > 0;
  const hasCv = Boolean(cvFile);
  const canSave = Boolean(role) && hasRoleSpec && validEmail && validName && !saving;

  const onDropFile = (event) => {
    event.preventDefault();
    setDragActive(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) setCvFile(file);
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Add candidate"
      description="Create a role application. CV upload is optional and can be added later."
      footer={(
        <div className="flex items-center justify-between gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            type="button"
            variant="primary"
            disabled={!canSave}
            onClick={() => {
              setTouched({ email: true, name: true, cv: touched.cv });
              if (!canSave) return;
              onSubmit({
                email: email.trim(),
                name: name.trim(),
                position: position.trim() || undefined,
                cvFile,
              });
            }}
          >
            {saving ? 'Saving...' : 'Add candidate'}
          </Button>
        </div>
      )}
    >
      {error ? (
        <Card className="mb-4 border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </Card>
      ) : null}

      <div className="space-y-4">
        <Card className="bg-[#faf8ff] px-3 py-2 text-sm text-gray-700">
          <span className="font-medium">Role:</span> {role?.name || 'No role selected'}
        </Card>

        {!hasRoleSpec ? (
          <Card className="border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            Upload a role job spec before adding candidates.
          </Card>
        ) : null}

        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-gray-800">Email *</span>
          <Input
            type="email"
            value={email}
            onBlur={() => setTouched((prev) => ({ ...prev, email: true }))}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="candidate@company.com"
            className={touched.email && !validEmail ? '!border-red-400 !bg-red-50' : ''}
          />
          {touched.email && !validEmail ? (
            <span className="mt-1 block text-xs text-red-700">Candidate email is required.</span>
          ) : null}
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-gray-800">Candidate name *</span>
          <Input
            type="text"
            value={name}
            onBlur={() => setTouched((prev) => ({ ...prev, name: true }))}
            onChange={(event) => setName(event.target.value)}
            placeholder="Jane Doe"
            className={touched.name && !validName ? '!border-red-400 !bg-red-50' : ''}
          />
          {touched.name && !validName ? (
            <span className="mt-1 block text-xs text-red-700">Candidate name is required.</span>
          ) : null}
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-gray-800">Candidate position</span>
          <Input
            type="text"
            value={position}
            onChange={(event) => setPosition(event.target.value)}
            placeholder="Defaults to role title"
          />
        </label>

        <div>
          <span className="mb-1 block text-sm font-semibold text-gray-800">CV upload (optional)</span>
          <span className="mb-1 block text-xs text-gray-500">Upload now or add later from the candidate row.</span>
          <label
            onDragEnter={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragOver={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={(event) => {
              event.preventDefault();
              setDragActive(false);
            }}
            onDrop={onDropFile}
            className={cx(
              'block border-2 border-dashed p-5 text-center transition',
              dragActive
                ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)]'
                : 'border-[var(--taali-border-muted)] bg-[var(--taali-surface)] hover:border-[var(--taali-border)]'
            )}
          >
            <UploadCloud size={20} className="mx-auto text-gray-500" />
            <span className="mt-2 block text-sm font-medium text-gray-700">
              {cvFile ? cvFile.name : 'Drop CV here or choose a file'}
            </span>
            <span className="mt-1 block text-xs text-gray-500">PDF or DOCX</span>
            <input
              type="file"
              accept=".pdf,.docx,.doc"
              onChange={(event) => {
                setTouched((prev) => ({ ...prev, cv: true }));
                setCvFile(event.target.files?.[0] || null);
              }}
              className="sr-only"
            />
          </label>
          {!hasCv ? (
            <span className="mt-1 block text-xs text-amber-700">No CV yet. Role fit scoring will show N/A until uploaded.</span>
          ) : null}
        </div>
      </div>
    </Sheet>
  );
};
