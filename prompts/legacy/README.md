# Legacy prompt files

These are the original prompts of the flat pipeline
(`core.generate_test_cases` / `application.legacy`). They prescribe the
historical output format: a bare JSON array with `testcase_name`, `actions` and
`expected_results`.

They must not be used with the current pipeline. Its contract is one JSON
object with `schema_version`, `test_cases` and `open_questions`, and a prompt
demanding the old array makes every answer invalid - the parser rejects it as
`E_SCHEMA`.

The files in `prompts/` carry the same technical content, rewritten as project
guidance without any format instructions. The application always supplies the
contract rules itself and appends guidance below them.
