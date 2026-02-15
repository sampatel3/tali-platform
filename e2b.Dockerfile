FROM e2bdev/code-interpreter:latest

# Preinstall Claude Code CLI so every assessment terminal has it available.
RUN npm install -g @anthropic-ai/claude-code \
    && claude --version
