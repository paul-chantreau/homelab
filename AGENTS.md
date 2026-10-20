# Language Requirements

Write everything in English only, including user-facing responses, documentation,
code comments, new identifiers, logs, error messages, commit messages, and pull
request titles and descriptions. Preserve existing identifiers, paths,
configuration values, and external interface contracts when changing them would
alter behavior or compatibility.

# Documentation Location

Write and maintain all project documentation in the root `README.md` only.
Update it when a change requires documentation. Do not create a `docs/` directory,
additional README files, or separate documentation, guides, or reports elsewhere.
Keep agent working instructions in the root `AGENTS.md`.

# Commit and Push Completed Changes

After completing a task and passing the relevant validation, commit and push the
task's changes to the intended remote branch before the final report. Do this
automatically without waiting for a separate push request, unless the user
explicitly asks to keep the changes local or uncommitted. Preserve unrelated
changes and existing remote commits; never force-push.

# Task Complexity Management

Before making any code changes, assess the complexity of the task.

## Complexity Classification

### SIMPLE
- Localized changes affecting a small number of files
- Clearly defined requirements and expected behavior
- Low risk of regressions or side effects
- Straightforward implementation and testing

### MEDIUM
- Changes involving multiple files or components
- Business logic modifications
- Dependencies between modules
- Integration tests may be required

### COMPLEX
- Significant architectural changes or refactoring
- Complex debugging or root cause analysis
- Security-sensitive changes, concurrency, or database migrations
- High risk of regressions or unintended side effects
- Multiple systems or services involved

## Execution Strategy

### SIMPLE
- Implement the changes directly
- Avoid unnecessary analysis or extensive planning
- Run only relevant tests
- Keep changes minimal and focused

### MEDIUM
- Identify affected files and dependencies
- Create a concise implementation plan
- Implement changes incrementally
- Run relevant unit and integration tests
- Verify that existing behavior is preserved

### COMPLEX
- Analyze the architecture and dependencies before implementation
- Identify major risks and potential side effects
- Break the task into smaller, manageable steps when appropriate
- Document important technical decisions
- Implement and validate changes incrementally
- Run comprehensive tests where feasible
- Review the final changes for regressions and security issues

## General Guidelines

- Prefer the simplest solution that meets the requirements
- Avoid unnecessary refactoring or scope expansion
- Reuse existing patterns and conventions
- Minimize unnecessary tool calls and token consumption
- Do not perform extensive analysis for straightforward tasks
- Escalate the complexity classification if unexpected difficulties arise
- Prioritize correctness, maintainability, and test coverage over speed
- Do not skip essential validation to reduce token usage

## Final Report

At the end of each task, provide a concise summary containing:

- **Complexity:** SIMPLE / MEDIUM / COMPLEX
- **Reason:** One sentence explaining the classification
- **Changes:** Summary of implemented modifications
- **Tests:** Tests executed and their results
- **Remaining Risks:** Known limitations or unresolved concerns, if any

Keep the final report proportional to the task complexity.
