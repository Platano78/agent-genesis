#!/usr/bin/env node

/**
 * Agent Genesis - Claude Configuration Helper
 *
 * Automatically configures Claude Desktop/Code to use the Agent Genesis MCP server.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

// Colors for terminal output
const colors = {
  reset: '\x1b[0m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  cyan: '\x1b[36m',
};

const log = {
  info: (msg) => console.log(`${colors.blue}[INFO]${colors.reset} ${msg}`),
  success: (msg) => console.log(`${colors.green}[SUCCESS]${colors.reset} ${msg}`),
  warn: (msg) => console.log(`${colors.yellow}[WARNING]${colors.reset} ${msg}`),
  error: (msg) => console.log(`${colors.red}[ERROR]${colors.reset} ${msg}`),
};

/**
 * Get the Claude configuration file path based on OS
 */
function getConfigPath() {
  const platform = os.platform();
  const home = os.homedir();

  switch (platform) {
    case 'darwin': // macOS
      return path.join(home, 'Library', 'Application Support', 'Claude', 'claude_desktop_config.json');
    case 'win32': // Windows
      return path.join(process.env.APPDATA || path.join(home, 'AppData', 'Roaming'), 'Claude', 'claude_desktop_config.json');
    case 'linux':
      // Check for WSL
      if (process.env.WSL_DISTRO_NAME || fs.existsSync('/proc/version') &&
          fs.readFileSync('/proc/version', 'utf8').toLowerCase().includes('microsoft')) {
        // WSL - use Windows path
        const winHome = execSync('cmd.exe /c "echo %USERPROFILE%"', { encoding: 'utf8' }).trim();
        const wslWinHome = winHome.replace(/\\/g, '/').replace(/^([A-Z]):/, (_, letter) => `/mnt/${letter.toLowerCase()}`);
        return path.join(wslWinHome, 'AppData', 'Roaming', 'Claude', 'claude_desktop_config.json');
      }
      return path.join(home, '.config', 'Claude', 'claude_desktop_config.json');
    default:
      return path.join(home, '.config', 'Claude', 'claude_desktop_config.json');
  }
}

/**
 * Check if Python and agent-genesis-mcp are installed
 */
function checkPythonMcp() {
  try {
    // Check if agent-genesis-mcp is installed
    execSync('agent-genesis-mcp --version 2>/dev/null || python -m agent_genesis_mcp --version 2>/dev/null', {
      stdio: 'pipe'
    });
    return { installed: true, command: 'agent-genesis-mcp' };
  } catch {
    try {
      // Check if python is available
      execSync('python --version', { stdio: 'pipe' });
      return { installed: false, pythonAvailable: true };
    } catch {
      return { installed: false, pythonAvailable: false };
    }
  }
}

/**
 * Get the MCP server configuration entry
 */
function getMcpConfig(command = 'agent-genesis-mcp') {
  return {
    command: command,
    args: []
  };
}

/**
 * Read existing Claude config or create default
 */
function readConfig(configPath) {
  try {
    if (fs.existsSync(configPath)) {
      const content = fs.readFileSync(configPath, 'utf8');
      return JSON.parse(content);
    }
  } catch (err) {
    log.warn(`Could not read existing config: ${err.message}`);
  }
  return { mcpServers: {} };
}

/**
 * Write config file
 */
function writeConfig(configPath, config) {
  const dir = path.dirname(configPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
}

/**
 * Main setup function
 */
function setup() {
  console.log('');
  console.log('╔═══════════════════════════════════════════════════════════════╗');
  console.log('║        Agent Genesis - Claude Configuration Helper            ║');
  console.log('╚═══════════════════════════════════════════════════════════════╝');
  console.log('');

  // Get config path
  const configPath = getConfigPath();
  log.info(`Claude config path: ${configPath}`);

  // Check Python/MCP installation
  const mcpStatus = checkPythonMcp();

  if (!mcpStatus.installed) {
    if (!mcpStatus.pythonAvailable) {
      log.error('Python is not installed. Please install Python 3.10+ first.');
      log.info('Visit: https://www.python.org/downloads/');
      process.exit(1);
    }

    log.warn('agent-genesis-mcp is not installed.');
    log.info('Installing agent-genesis-mcp...');

    try {
      execSync('pip install agent-genesis-mcp', { stdio: 'inherit' });
      log.success('agent-genesis-mcp installed successfully!');
    } catch (err) {
      log.error('Failed to install agent-genesis-mcp.');
      log.info('Try manually: pip install agent-genesis-mcp');
      process.exit(1);
    }
  } else {
    log.success('agent-genesis-mcp is installed');
  }

  // Read existing config
  const config = readConfig(configPath);

  // Check if already configured
  if (config.mcpServers && config.mcpServers['agent-genesis']) {
    log.warn('Agent Genesis is already configured in Claude.');

    const readline = require('readline');
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout
    });

    rl.question('Overwrite existing configuration? [y/N]: ', (answer) => {
      rl.close();
      if (answer.toLowerCase() !== 'y') {
        log.info('Configuration unchanged.');
        process.exit(0);
      }
      finishSetup(configPath, config);
    });
    return;
  }

  finishSetup(configPath, config);
}

function finishSetup(configPath, config) {
  // Add Agent Genesis MCP server
  if (!config.mcpServers) {
    config.mcpServers = {};
  }

  config.mcpServers['agent-genesis'] = getMcpConfig();

  // Write config
  try {
    writeConfig(configPath, config);
    log.success('Claude configuration updated!');
  } catch (err) {
    log.error(`Failed to write config: ${err.message}`);
    log.info('You may need to run with sudo or adjust permissions.');
    process.exit(1);
  }

  console.log('');
  console.log('╔═══════════════════════════════════════════════════════════════╗');
  console.log('║                    Setup Complete!                            ║');
  console.log('╚═══════════════════════════════════════════════════════════════╝');
  console.log('');
  log.info('Next steps:');
  console.log('  1. Ensure Agent Genesis API is running:');
  console.log('     docker-compose up -d');
  console.log('');
  console.log('  2. Restart Claude Desktop/Code');
  console.log('');
  console.log('  3. Ask Claude: "Search my conversation history for authentication"');
  console.log('');
}

/**
 * Check-only mode (for postinstall)
 */
function checkOnly() {
  const mcpStatus = checkPythonMcp();
  if (mcpStatus.installed) {
    log.success('agent-genesis-mcp is installed');
    const configPath = getConfigPath();
    const config = readConfig(configPath);
    if (config.mcpServers && config.mcpServers['agent-genesis']) {
      log.success('Claude is configured to use Agent Genesis');
    } else {
      log.info('Run "npx agent-genesis-config setup" to configure Claude');
    }
  } else {
    log.info('Run "npx agent-genesis-config setup" to install and configure');
  }
}

/**
 * Show status
 */
function status() {
  console.log('');
  log.info('Agent Genesis Status');
  console.log('');

  // Check MCP installation
  const mcpStatus = checkPythonMcp();
  console.log(`MCP Server: ${mcpStatus.installed ? colors.green + 'Installed' : colors.yellow + 'Not installed'}${colors.reset}`);

  // Check config
  const configPath = getConfigPath();
  console.log(`Config path: ${configPath}`);

  const config = readConfig(configPath);
  const isConfigured = config.mcpServers && config.mcpServers['agent-genesis'];
  console.log(`Claude config: ${isConfigured ? colors.green + 'Configured' : colors.yellow + 'Not configured'}${colors.reset}`);

  // Check API
  try {
    execSync('curl -s http://localhost:8080/health', { stdio: 'pipe' });
    console.log(`API status: ${colors.green}Running${colors.reset}`);
  } catch {
    console.log(`API status: ${colors.red}Not running${colors.reset}`);
  }

  console.log('');
}

/**
 * Remove configuration
 */
function remove() {
  const configPath = getConfigPath();
  const config = readConfig(configPath);

  if (config.mcpServers && config.mcpServers['agent-genesis']) {
    delete config.mcpServers['agent-genesis'];
    writeConfig(configPath, config);
    log.success('Agent Genesis removed from Claude configuration');
  } else {
    log.info('Agent Genesis is not configured');
  }
}

// Parse command line arguments
const args = process.argv.slice(2);
const command = args[0] || 'setup';

switch (command) {
  case 'setup':
    setup();
    break;
  case 'status':
    status();
    break;
  case 'remove':
    remove();
    break;
  case '--check-only':
    checkOnly();
    break;
  case '--help':
  case '-h':
    console.log(`
Agent Genesis - Claude Configuration Helper

Usage: npx @agent-genesis/claude-config [command]

Commands:
  setup     Configure Claude to use Agent Genesis (default)
  status    Show current configuration status
  remove    Remove Agent Genesis from Claude configuration
  --help    Show this help message

Examples:
  npx @agent-genesis/claude-config setup
  npx @agent-genesis/claude-config status
`);
    break;
  default:
    log.error(`Unknown command: ${command}`);
    log.info('Use --help for usage information');
    process.exit(1);
}
