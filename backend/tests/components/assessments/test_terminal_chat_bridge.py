from app.domains.assessments_runtime import candidate_terminal_routes


def test_build_terminal_chat_command_wraps_prompt_for_ui_bridge():
    command = candidate_terminal_routes._build_terminal_chat_command(
        request_id="req-123",
        message="Explain the failing test and patch it.",
        selected_file_path="src/main.py",
    )

    assert "taali_ui_chat req-123 src/main.py" in command
    assert "Explain the failing test and patch it." in command
    assert "TAALI_CLAUDE_PROMPT_req-123_" in command


def test_consume_terminal_chat_output_strips_markers_and_captures_clean_reply():
    state = {
        "line_buffer": "",
        "active_request_id": None,
        "active_output": [],
    }

    filtered, completed = candidate_terminal_routes._consume_terminal_chat_output(
        (
            "user@host:~$ taali_ui_chat 'req-123' 'src/main.py'\n"
            "TAALI_CLAUDE_CHAT_BEGIN req-123\n"
            "I found the issue in \x1b[35msrc/main.py\x1b[0m.\n"
            "Add a null check before the retry loop.\n"
            "TAALI_CLAUDE_CHAT_END req-123 0\n"
            "user@host:~$ "
        ),
        state,
    )

    assert "TAALI_CLAUDE_CHAT_BEGIN" not in filtered
    assert "TAALI_CLAUDE_CHAT_END" not in filtered
    assert "user@host:~$ taali_ui_chat" in filtered
    assert "Add a null check before the retry loop." in filtered
    assert completed == [
        {
            "request_id": "req-123",
            "content": "I found the issue in src/main.py.\nAdd a null check before the retry loop.",
            "exit_status": 0,
        }
    ]
