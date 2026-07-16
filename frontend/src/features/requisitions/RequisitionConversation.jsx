import { FileText, Paperclip, X } from 'lucide-react';

import { ChatComposer, ChatMarkdown, ChatMessage, ThinkingDots } from '../../shared/chat';
import { MotionSpinner } from '../../shared/motion';

const ACCEPT = '.txt,.vtt,.srt,.md,.pdf,image/*';

function RequisitionTurn({ message }) {
  const attachments = Array.isArray(message.attachments) ? message.attachments : [];
  if (message.role === 'user') {
    return (
      <div className="tk-msg-user-wrap">
        <div className="tk-msg-user">
          {message.content}
          {attachments.length > 0 ? (
            <div className="rq-attach-row" style={{ marginTop: message.content ? 8 : 0, marginBottom: 0 }}>
              {attachments.map((attachment, index) => (
                <span
                  key={index}
                  className="rq-attach-chip"
                  style={{
                    background: 'rgba(255,255,255,0.12)',
                    borderColor: 'rgba(255,255,255,0.2)',
                    color: 'var(--taali-on-accent)',
                  }}
                >
                  <span className="rq-attach-glyph"><FileText size={13} /></span>
                  <span className="rq-attach-name">{attachment.name}</span>
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    );
  }
  return (
    <ChatMessage role="assistant">
      <div className="rq-agent-say">
        <span className="rq-who">Agent</span>
        <ChatMarkdown>{message.content}</ChatMarkdown>
      </div>
    </ChatMessage>
  );
}

export function RequisitionConversation({
  applied,
  attachments,
  canSend,
  composer,
  fileInputRef,
  messages,
  onComposerChange,
  onComposerSubmit,
  onFilePick,
  onPaste,
  onQuickReply,
  onRemoveAttachment,
  onSendAttachments,
  quickReplies,
  relatedRoleDraft,
  threadEndRef,
  turnInFlight,
}) {
  return (
    <div className="rq-convo">
      <div className="rq-thread">
        {messages.map((message, index) => (
          <RequisitionTurn key={index} message={message} />
        ))}
        {turnInFlight ? (
          <ChatMessage role="assistant"><ThinkingDots label="thinking…" /></ChatMessage>
        ) : null}
        <div ref={threadEndRef} />
      </div>

      {applied ? (
        <div className="rq-applied-note" role="note">
          {relatedRoleDraft
            ? 'This related-role conversation is archived. Continue work in the created role.'
            : 'This intake conversation is archived. Continue changes in the live job.'}
        </div>
      ) : (
        <div className="rq-composer-wrap">
          {attachments.length > 0 ? (
            <div className="rq-attach-row">
              {attachments.map((attachment) => (
                <span key={attachment.id} className="rq-attach-chip">
                  {attachment.url ? (
                    <img className="rq-attach-thumb" src={attachment.url} alt={attachment.file.name} />
                  ) : (
                    <span className="rq-attach-glyph"><FileText size={14} /></span>
                  )}
                  <span className="rq-attach-name">{attachment.file.name}</span>
                  <button
                    type="button"
                    className="rq-attach-x"
                    aria-label={`Remove ${attachment.file.name}`}
                    onClick={() => onRemoveAttachment(attachment.id)}
                  >
                    <X size={13} />
                  </button>
                </span>
              ))}
            </div>
          ) : null}

          <div className="rq-composer-tools">
            <button
              type="button"
              className="rq-attach-btn"
              onClick={() => fileInputRef.current?.click()}
              disabled={turnInFlight}
            >
              <Paperclip size={14} /> Attach
            </button>
            <span className="rq-attach-hint">transcript or JD screenshot · or paste an image</span>
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPT}
              multiple
              hidden
              onChange={onFilePick}
            />
          </div>

          {quickReplies.length > 0 ? (
            <div className="rq-quick-replies">
              {quickReplies.map((reply, index) => (
                <button
                  key={`${reply.text}-${index}`}
                  type="button"
                  className="rq-quick-chip"
                  onClick={() => onQuickReply(reply.text, reply.deterministic)}
                  disabled={turnInFlight}
                >
                  {reply.text}
                </button>
              ))}
            </div>
          ) : null}

          <ChatComposer
            value={composer}
            onChange={onComposerChange}
            onSubmit={onComposerSubmit}
            onPaste={onPaste}
            placeholder={relatedRoleDraft
              ? 'Tell the agent what changes from the original role…'
              : 'Tell the agent about the role, or answer its question…'}
            busy={turnInFlight}
          />

          {composer.trim() === '' && attachments.length > 0 ? (
            <div style={{ marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
              <button
                type="button"
                className="rq-btn-sm is-primary"
                onClick={onSendAttachments}
                disabled={!canSend}
              >
                {turnInFlight ? <MotionSpinner className="rq-motion-spinner" size={15} /> : null}{' '}
                Send {attachments.length} attachment{attachments.length === 1 ? '' : 's'}
              </button>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

export default RequisitionConversation;
