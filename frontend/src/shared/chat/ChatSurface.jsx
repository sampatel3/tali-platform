import { forwardRef } from 'react';

/**
 * Canonical boundary for every Taali conversation.
 *
 * Feature CSS owns the surrounding page/rail layout; everything inside this
 * boundary gets the shared chat spacing, type, colour and density contract.
 */
export const ChatSurface = forwardRef(function ChatSurface({
  as: Component = 'div',
  density = 'comfortable',
  tone = 'default',
  className = '',
  children,
  ...props
}, ref) {
  return (
    <Component
      ref={ref}
      className={`tk-chat${className ? ` ${className}` : ''}`}
      data-chat-density={density}
      data-chat-tone={tone}
      {...props}
    >
      {children}
    </Component>
  );
});

export default ChatSurface;
