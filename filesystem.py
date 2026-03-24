import os
import subprocess
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("filesystem-command")

# Constants
ALLOWED_EXTENSIONS = {'.txt', '.py', '.java', '.js', '.json', '.md', '.csv', '.log', '.yaml', '.yml', '.xml', '.html', '.css', '.sh', '.bat', '.clj', '.edn', '.cljs', '.cljc', '.dump'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
BLOCKED_COMMANDS = {'rm', 'del', 'format', 'mkfs', 'dd', 'shutdown', 'reboot', 'halt', 'poweroff'}
DEFAULT_ENCODING = 'utf-8'

def is_safe_path(path: str) -> bool:
    """Check if the path is safe (no directory traversal)."""
    try:
        resolved_path = Path(path).resolve()
        return not any(part.startswith('..') for part in Path(path).parts)
    except Exception:
        return False

def is_allowed_file(path: str) -> bool:
    """Check if file extension is allowed."""
    return Path(path).suffix.lower() in ALLOWED_EXTENSIONS

def is_safe_command(command: str) -> bool:
    """Check if command is safe to execute."""
    cmd_parts = command.strip().split()
    if not cmd_parts:
        return False
    
    base_command = cmd_parts[0].lower()
    return base_command not in BLOCKED_COMMANDS

async def read_file_content(file_path: str) -> str | None:
    """Read file content with multiple encoding attempts."""
    encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1', 'cp1252']
    
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except Exception:
            return None
    
    return None

async def execute_system_command(command: str, cwd: str, timeout: int = 30) -> Dict[str, Any]:
    """Execute system command safely with timeout."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace'
        )
        
        return {
            'success': True,
            'returncode': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr
        }
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': f'Command timed out after {timeout} seconds',
            'returncode': -1
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'returncode': -1
        }

@mcp.tool()
async def read_file(file_path: str) -> str:
    """Read the contents of a text file.
    
    Args:
        file_path: Path to the file to read
    """
    if not is_safe_path(file_path):
        return f"Error: Unsafe file path: {file_path}"
    
    if not is_allowed_file(file_path):
        return f"Error: File type not allowed: {Path(file_path).suffix}"
    
    path = Path(file_path)
    if not path.exists():
        return f"Error: File does not exist: {file_path}"
    
    if not path.is_file():
        return f"Error: Path is not a file: {file_path}"
    
    if path.stat().st_size > MAX_FILE_SIZE:
        return f"Error: File too large (>{MAX_FILE_SIZE} bytes): {file_path}"
    
    content = await read_file_content(str(path))
    if content is None:
        return f"Error: Unable to read file with supported encodings: {file_path}"
    
    return f"File: {file_path}\nSize: {len(content)} characters\n\n{content}"

@mcp.tool()
async def write_file(file_path: str, content: str, encoding: str = DEFAULT_ENCODING) -> str:
    """Write content to a text file.
    
    Args:
        file_path: Path to the file to write
        content: Content to write to the file
        encoding: File encoding (default: utf-8)
    """
    if not is_safe_path(file_path):
        return f"Error: Unsafe file path: {file_path}"
    
    if not is_allowed_file(file_path):
        return f"Error: File type not allowed: {Path(file_path).suffix}"
    
    if len(content.encode(encoding)) > MAX_FILE_SIZE:
        return f"Error: Content too large (>{MAX_FILE_SIZE} bytes)"
    
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, 'w', encoding=encoding) as f:
            f.write(content)
        
        return f"Successfully wrote {len(content)} characters to: {file_path}"
    
    except Exception as e:
        return f"Error writing file: {str(e)}"

@mcp.tool()
async def append_file(file_path: str, content: str, encoding: str = DEFAULT_ENCODING) -> str:
    """Append content to a text file.
    
    Args:
        file_path: Path to the file to append to
        content: Content to append to the file
        encoding: File encoding (default: utf-8)
    """
    if not is_safe_path(file_path):
        return f"Error: Unsafe file path: {file_path}"
    
    if not is_allowed_file(file_path):
        return f"Error: File type not allowed: {Path(file_path).suffix}"
    
    try:
        path = Path(file_path)
        
        # Check final file size
        current_size = path.stat().st_size if path.exists() else 0
        if current_size + len(content.encode(encoding)) > MAX_FILE_SIZE:
            return f"Error: File would exceed size limit (>{MAX_FILE_SIZE} bytes)"
        
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, 'a', encoding=encoding) as f:
            f.write(content)
        
        return f"Successfully appended {len(content)} characters to: {file_path}"
    
    except Exception as e:
        return f"Error appending to file: {str(e)}"

@mcp.tool()
async def list_directory(directory_path: str = ".", show_hidden: bool = False) -> str:
    """List the contents of a directory.
    
    Args:
        directory_path: Path to the directory (default: current directory)
        show_hidden: Whether to show hidden files (default: False)
    """
    if not is_safe_path(directory_path):
        return f"Error: Unsafe directory path: {directory_path}"
    
    path = Path(directory_path)
    if not path.exists():
        return f"Error: Directory does not exist: {directory_path}"
    
    if not path.is_dir():
        return f"Error: Path is not a directory: {directory_path}"
    
    try:
        items = []
        for item in sorted(path.iterdir()):
            if not show_hidden and item.name.startswith('.'):
                continue
            
            try:
                stat = item.stat()
                size = stat.st_size if item.is_file() else 0
                item_type = "FILE" if item.is_file() else "DIR "
                items.append(f"{item_type} {item.name:<40} {size:>10,} bytes")
            except Exception:
                items.append(f"ERR  {item.name:<40} {'Access denied':>10}")
        
        if not items:
            return f"Directory is empty: {directory_path}"
        
        header = f"Contents of: {path.absolute()}\n{'Type':<4} {'Name':<40} {'Size':>15}\n{'-' * 60}"
        return f"{header}\n" + "\n".join(items)
    
    except Exception as e:
        return f"Error listing directory: {str(e)}"

@mcp.tool()
async def get_file_info(file_path: str) -> str:
    """Get detailed information about a file or directory.
    
    Args:
        file_path: Path to the file or directory
    """
    if not is_safe_path(file_path):
        return f"Error: Unsafe path: {file_path}"
    
    path = Path(file_path)
    if not path.exists():
        return f"Error: Path does not exist: {file_path}"
    
    try:
        stat = path.stat()
        import time
        
        info_lines = [
            f"Path: {path.absolute()}",
            f"Name: {path.name}",
            f"Type: {'File' if path.is_file() else 'Directory'}",
            f"Size: {stat.st_size:,} bytes" if path.is_file() else "Size: N/A (directory)",
            f"Created: {time.ctime(stat.st_ctime)}",
            f"Modified: {time.ctime(stat.st_mtime)}",
            f"Accessed: {time.ctime(stat.st_atime)}",
        ]
        
        if path.is_file():
            info_lines.extend([
                f"Extension: {path.suffix or 'None'}",
                f"Readable: {os.access(path, os.R_OK)}",
                f"Writable: {os.access(path, os.W_OK)}",
                f"Executable: {os.access(path, os.X_OK)}",
            ])
        
        return "\n".join(info_lines)
    
    except Exception as e:
        return f"Error getting file info: {str(e)}"

@mcp.tool()
async def execute_command(command: str, working_directory: str = ".", timeout: int = 30) -> str:
    """Execute a system command safely.
    
    Args:
        command: Command to execute
        working_directory: Working directory for the command (default: current directory)
        timeout: Timeout in seconds (default: 30)
    """
    if not is_safe_command(command):
        return f"Error: Command not allowed for security reasons: {command.split()[0] if command.split() else 'empty'}"
    
    if not is_safe_path(working_directory):
        return f"Error: Unsafe working directory: {working_directory}"
    
    work_dir = Path(working_directory)
    if not work_dir.exists() or not work_dir.is_dir():
        return f"Error: Working directory does not exist or is not a directory: {working_directory}"
    
    result = await execute_system_command(command, str(work_dir.absolute()), timeout)
    
    output_lines = [
        f"Command: {command}",
        f"Working Directory: {work_dir.absolute()}",
        f"Return Code: {result.get('returncode', 'N/A')}",
    ]
    
    if not result['success']:
        output_lines.append(f"Error: {result.get('error', 'Unknown error')}")
        return "\n".join(output_lines)
    
    if result.get('stdout'):
        output_lines.append(f"\nStandard Output:\n{result['stdout']}")
    
    if result.get('stderr'):
        output_lines.append(f"\nError Output:\n{result['stderr']}")
    
    return "\n".join(output_lines)

@mcp.tool()
async def get_current_directory() -> str:
    """Get the current working directory.
    """
    try:
        return f"Current working directory: {Path.cwd().absolute()}"
    except Exception as e:
        return f"Error getting current directory: {str(e)}"

@mcp.tool()
async def create_directory(directory_path: str) -> str:
    """Create a new directory (including parent directories if needed).
    
    Args:
        directory_path: Path of the directory to create
    """
    if not is_safe_path(directory_path):
        return f"Error: Unsafe directory path: {directory_path}"
    
    try:
        path = Path(directory_path)
        path.mkdir(parents=True, exist_ok=True)
        return f"Successfully created directory: {path.absolute()}"
    
    except Exception as e:
        return f"Error creating directory: {str(e)}"

@mcp.tool()
async def search_files_ag(
    pattern: str,
    search_path: str = ".",
    file_type: Optional[str] = None,
    case_sensitive: bool = False,
    max_results: int = 100,
    context_lines: int = 0
) -> str:
    """Search for text patterns in files using ag (The Silver Searcher).
    
    Args:
        pattern: Text pattern to search for (supports regex)
        search_path: Directory to search in (default: current directory)
        file_type: File type filter (e.g., 'py', 'js', 'clj') (default: None)
        case_sensitive: Whether to perform case-sensitive search (default: False)
        max_results: Maximum number of results to return (default: 100)
        context_lines: Number of context lines to show before/after match (default: 0)
    """
    if not is_safe_path(search_path):
        return f"Error: Unsafe search path: {search_path}"
    
    path = Path(search_path)
    if not path.exists():
        return f"Error: Search path does not exist: {search_path}"
    
    if not path.is_dir():
        return f"Error: Search path is not a directory: {search_path}"
    
    # Check if ag is available
    try:
        subprocess.run(['ag', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "Error: 'ag' (The Silver Searcher) is not installed. Please install it first:\n" \
               "  macOS: brew install the_silver_searcher\n" \
               "  Linux: apt-get install silversearcher-ag or yum install the_silver_searcher\n" \
               "  Windows: choco install ag"
    
    # Build ag command
    ag_cmd = ['ag']
    
    # Add case sensitivity flag
    if not case_sensitive:
        ag_cmd.append('-i')
    
    # Add context lines
    if context_lines > 0:
        ag_cmd.extend(['-C', str(context_lines)])
    
    # Add file type filter
    if file_type:
        ag_cmd.extend(['--' + file_type.lstrip('.')])
    
    # Add max count
    ag_cmd.extend(['-m', str(max_results)])
    
    # Add color output for better readability
    ag_cmd.append('--color')
    
    # Add line numbers
    ag_cmd.append('--numbers')
    
    # Add the pattern
    ag_cmd.append(pattern)
    
    # Add search path
    ag_cmd.append(str(path.absolute()))
    
    try:
        result = subprocess.run(
            ag_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding='utf-8',
            errors='replace'
        )
        
        # ag returns 0 if matches found, 1 if no matches, >1 for errors
        if result.returncode > 1:
            return f"Error running ag: {result.stderr}"
        
        if result.returncode == 1 or not result.stdout.strip():
            return f"No matches found for pattern: {pattern}\nSearch path: {path.absolute()}"
        
        output_lines = [
            f"Search Results for: {pattern}",
            f"Search Path: {path.absolute()}",
            f"Case Sensitive: {case_sensitive}",
        ]
        
        if file_type:
            output_lines.append(f"File Type: {file_type}")
        
        output_lines.append(f"\n{'-' * 80}\n")
        output_lines.append(result.stdout)
        
        # Count matches
        match_count = result.stdout.count('\n')
        output_lines.append(f"\n{'-' * 80}")
        output_lines.append(f"Total lines matched: {match_count}")
        
        return "\n".join(output_lines)
    
    except subprocess.TimeoutExpired:
        return f"Error: Search timed out after 30 seconds"
    except Exception as e:
        return f"Error executing ag search: {str(e)}"

if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')
