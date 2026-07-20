FROM e2bdev/code-interpreter:latest

# Assessment sandboxes run without network access.  Every dependency declared
# by a canonical task must therefore be present in the image before the
# workspace is provisioned.  ``test_task_offline_bootstrap.py`` keeps this list
# aligned with the requirements embedded in the task catalogue.
RUN python3 -m pip install --no-cache-dir \
        'pytest>=8.0,<9' \
        'python-hcl2>=4.3.4,<8' \
    && python3 -I -c "import hcl2, pytest"

# Preinstall Claude Code CLI so every assessment terminal has it available.
RUN npm install -g @anthropic-ai/claude-code \
    && claude --version
