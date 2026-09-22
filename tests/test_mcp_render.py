"""Tests for turning kernel outputs into MCP content."""

from agent_runtime.mcp.render import MAX_IMAGES, MAX_TEXT_CHARS, render_execution, strip_ansi


def png(data: str = "iVBORw0KGgo=") -> dict:
    return {"type": "display_data", "content": {"data": {"image/png": data}}}


class TestText:
    def test_stream_output_becomes_text(self):
        blocks = render_execution(
            {"outputs": [{"type": "stream", "content": {"name": "stdout", "text": "hello\n"}}]}
        )

        assert [b.type for b in blocks] == ["text"]
        assert blocks[0].text == "hello"

    def test_a_result_uses_its_readable_representation(self):
        blocks = render_execution(
            {"outputs": [{"type": "result", "content": {"data": {"text/plain": "42"}}}]}
        )

        assert blocks[0].text == "42"

    def test_the_documented_flat_shape_is_accepted_too(self):
        blocks = render_execution(
            {
                "outputs": [
                    {"output_type": "stream", "name": "stdout", "text": "one\n"},
                    {"output_type": "execute_result", "data": {"text/plain": "two"}},
                ]
            }
        )

        assert blocks[0].text == "one\ntwo"

    def test_a_cell_with_no_output_still_says_something(self):
        blocks = render_execution({"outputs": [], "error": None, "execution_count": 3})

        assert len(blocks) == 1
        assert "no output" in blocks[0].text

    def test_long_output_is_truncated_from_the_middle(self):
        blocks = render_execution(
            {"outputs": [{"type": "stream", "content": {"text": "x" * 40_000}}]}
        )

        text = blocks[0].text
        assert len(text) < 40_000
        assert "characters omitted" in text
        assert text.startswith("x") and text.endswith("x")

    def test_short_output_is_left_alone(self):
        body = "y" * (MAX_TEXT_CHARS - 10)
        blocks = render_execution({"outputs": [{"type": "stream", "content": {"text": body}}]})

        assert blocks[0].text == body


class TestErrors:
    def test_a_traceback_comes_back_as_content_not_a_failure(self):
        blocks = render_execution(
            {
                "outputs": [],
                "success": False,
                "error": {
                    "ename": "ValueError",
                    "evalue": "bad",
                    "traceback": ["Traceback (most recent call last)", "ValueError: bad"],
                },
            }
        )

        assert "ValueError: bad" in blocks[0].text

    def test_terminal_colors_are_stripped(self):
        blocks = render_execution(
            {
                "outputs": [],
                "error": {
                    "ename": "ValueError",
                    "evalue": "bad",
                    "traceback": ["\x1b[0;31mValueError\x1b[0m: bad"],
                },
            }
        )

        assert "\x1b" not in blocks[0].text
        assert blocks[0].text == "ValueError: bad"

    def test_an_error_without_a_traceback_still_reads(self):
        blocks = render_execution({"outputs": [], "error": {"ename": "KeyError", "evalue": "k"}})

        assert blocks[0].text == "KeyError: k"

    def test_strip_ansi_leaves_ordinary_text(self):
        assert strip_ansi("plain text") == "plain text"


class TestImages:
    def test_a_figure_comes_back_as_an_image(self):
        blocks = render_execution({"outputs": [png()]})

        assert [b.type for b in blocks] == ["image"]
        assert blocks[0].mime_type == "image/png"
        assert blocks[0].data == "iVBORw0KGgo="

    def test_wrapped_base64_is_unwrapped(self):
        blocks = render_execution({"outputs": [png("iVBO\nRw0K\nGgo=")]})

        assert blocks[0].data == "iVBORw0KGgo="

    def test_text_comes_before_images(self):
        blocks = render_execution(
            {"outputs": [png(), {"type": "stream", "content": {"text": "plotting\n"}}]}
        )

        assert [b.type for b in blocks] == ["text", "image"]

    def test_too_many_images_are_capped_and_counted(self):
        blocks = render_execution({"outputs": [png()] * (MAX_IMAGES + 2)})

        assert sum(b.type == "image" for b in blocks) == MAX_IMAGES
        assert "2 further image" in blocks[-1].text
