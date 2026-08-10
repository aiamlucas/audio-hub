This project has been created as part of the 42 curriculum by ssin and lbueno-m.

# minishell

## Description

minishell is a minimal Unix shell written in C. The goal is to understand how a shell works by implementing one from scratch and having deal with tokenization, parsing, variable expansion, redirection, pipes, signals, execution and built-in commands.

The shell supports:
- Interactive prompt with command history
- Variable expansion (`$VAR`, `$?`)
- Single and double quote handling
- Input/output redirections (`<`, `>`, `>>`)
- Heredoc (`<<`) with and without variable expansion
- Pipelines (`|`) with multiple commands
- Built-in commands: `echo`, `cd`, `pwd`, `export`, `unset`, `env`, `exit`
- Signal handling (`Ctrl+C`, `Ctrl+\`, `Ctrl+D`)

---

## Instructions

### Compilation

```
make
```

### Running

```
./minishell
```

### Debug build

```
make DEBUG=1
```

---

## Features
 
### Built-in commands
 
| Command  | Description                              |
|----------|------------------------------------------|
| `echo`   | Print arguments, supports `-n` flag      |
| `cd`     | Change directory, supports `cd -`        |
| `pwd`    | Print current working directory          |
| `export` | Set environment variables                |
| `unset`  | Remove environment variables             |
| `env`    | Print all environment variables          |
| `exit`   | Exit the shell with optional exit code   |
 
### Redirections
 
| Operator | Description                   |
|----------|-------------------------------|
| `<`      | Redirect stdin from file      |
| `>`      | Redirect stdout to file       |
| `>>`     | Append stdout to file         |
| `<<`     | Heredoc: read until delimiter|
 
### Variable expansion
 
```
echo $HOME          # expands to home directory
echo $?             # expands to last exit code
echo "$VAR"         # expands inside double quotes
echo '$VAR'         # literal, no expansion in single quotes
cat << 'EOF'        # quoted heredoc delimiter: no expansion inside
```
 
---
 
## Architecture
 
The shell processes each input line through a pipeline of stages:
 
```
readline input
    │
    ▼
lexer()             tokenize raw input into a token list
    │
    ▼
expand_tokens()     replace $VAR and $? with their values
    │
    ▼
remove_quotes()     strip quote characters, preserve content
    │
    ▼
parser()            build command list with argv and redirections
    │
    ▼
process_all_heredocs()   read heredoc content into pipes
    │
    ▼
execute_command()   fork, execve, or run builtins directly
```
 
---

## Resources

- Advanced Programming in the UNIX Environment, 3rd Edition by W. Richard Stevens, Stephen A. Rago (direct from the 42 Berlin Library)
- Linux/Unix-Systemprogrammierung by Helmut Herold
- GNU Bash Manual (https://www.gnu.org/software/bash/manual/bash.html)
- Man pages: fork, execve, pipe, dup2, waitpid, sigaction
- https://www.youtube.com/@CodeVault

### AI usage

Chatgpt and Claude

- Testing: generate tests, especially for getting edge cases in expansion, heredoc and redirection
- Documentation: help to write this README
- Understanding concepts: help to understand core concepts like pipes, fork, file descriptors, signals, etc.

All code was written, understood and validated by the project authors. AI was used as learning and review tool. 
# audio-hub
