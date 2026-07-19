// Backend chat rows contain Anthropic-shaped content blocks. These helpers
// convert them to the flatter UI shape and reconcile persisted tool-result
// echoes with their preceding tool calls.

export const hydrateMessage = (message) => {
  const parts = [];
  const blocks = Array.isArray(message.content) ? message.content : [];
  for (const block of blocks) {
    if (block.type === 'text' && block.text) {
      parts.push({ type: 'text', text: block.text });
    }
    if (block.type === 'tool_use') {
      parts.push({
        type: 'tool_call',
        toolCallId: block.id,
        toolName: block.name,
        args: block.input || {},
        status: 'complete',
      });
    }
  }
  return {
    id: `m_${message.id}`,
    role: message.role === 'assistant' ? 'assistant' : 'user',
    parts,
    createdAt: message.created_at || null,
    _isToolResultEcho:
      blocks.length > 0 && blocks.every((block) => block.type === 'tool_result'),
    _toolResults: blocks.filter((block) => block.type === 'tool_result'),
  };
};

export const stitchToolResults = (rows) => {
  const stitched = [];
  for (const message of rows) {
    if (message._isToolResultEcho && stitched.length) {
      const previous = stitched[stitched.length - 1];
      let matched = false;
      const mergedParts = previous.parts.map((part) => {
        if (part.type !== 'tool_call') return part;
        const result = message._toolResults.find(
          (candidate) => candidate.tool_use_id === part.toolCallId,
        );
        if (!result) return part;
        matched = true;
        let parsed = result.content;
        try {
          parsed = JSON.parse(result.content);
        } catch {
          /* keep non-JSON tool output as a string */
        }
        return {
          ...part,
          result: parsed,
          status: result.is_error ? 'error' : 'complete',
        };
      });
      if (matched) {
        stitched[stitched.length - 1] = { ...previous, parts: mergedParts };
        continue;
      }
    }
    stitched.push(message);
  }

  return stitched
    .filter(
      (message) =>
        message._isToolResultEcho ||
        !(message.role === 'user' && !message.parts.length),
    )
    .map((message) => {
      // A page can begin with the result half of a tool call. Keep that row
      // invisibly until an older page supplies the matching call; dropping it
      // here would permanently lose the result at that page boundary.
      if (message._isToolResultEcho) {
        return { ...message, _historyHidden: true };
      }
      const {
        _isToolResultEcho,
        _toolResults,
        _historyHidden,
        ...visibleMessage
      } = message;
      return visibleMessage;
    });
};
