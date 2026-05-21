const crypto = require('crypto');

const DEFAULT_BYTE_LENGTH = 32;
const DEFAULT_ENCODING = 'base64';

const HELP_TEXT = `Usage: node generateJWTSecret.js [options] [bytes]

Script location: ./generateJWTSecret.js

Generate a cryptographically strong secret suitable for HS256 (HMAC-SHA256) signing.
Default: ${DEFAULT_BYTE_LENGTH} bytes; output encoding: ${DEFAULT_ENCODING} (standard base64 text, no decoration).

Options:
  -b, --bytes <n>        Number of random bytes to generate (default: ${DEFAULT_BYTE_LENGTH})
  -s, --size <n>         Alias for --bytes (optional)
  -e, --encoding <enc>   Buffer encoding for the secret (default: ${DEFAULT_ENCODING})
  -h, --help             Show this help message

Legacy positional arguments are still supported for compatibility:
  node generateJWTSecret.js [bytes] [encoding]

Examples:
  node generateJWTSecret.js            # prints ${DEFAULT_BYTE_LENGTH} bytes as base64
  node generateJWTSecret.js -s 64      # prints 64 bytes as base64
`;

function normalizeByteLength(value) {
  const byteLength = Number(value);

  if (!Number.isInteger(byteLength) || byteLength <= 0) {
    throw new Error(`Invalid byte length: ${value}`);
  }

  return byteLength;
}

function normalizeEncoding(value) {
  if (typeof value !== 'string' || value.length === 0 || !Buffer.isEncoding(value)) {
    throw new Error(`Invalid encoding: ${value}`);
  }

  return value;
}

function resolveOptions(input, maybeEncoding) {
  if (input && typeof input === 'object' && !Array.isArray(input)) {
    return {
      byteLength:
        input.byteLength === undefined ? DEFAULT_BYTE_LENGTH : normalizeByteLength(input.byteLength),
      encoding: input.encoding === undefined ? DEFAULT_ENCODING : normalizeEncoding(input.encoding),
    };
  }

  return {
    byteLength: input === undefined ? DEFAULT_BYTE_LENGTH : normalizeByteLength(input),
    encoding: maybeEncoding === undefined ? DEFAULT_ENCODING : normalizeEncoding(maybeEncoding),
  };
}

function generateJWTSecret(input, maybeEncoding) {
  const { byteLength, encoding } = resolveOptions(input, maybeEncoding);
  return crypto.randomBytes(byteLength).toString(encoding);
}

function parseArgs(argv) {
  const result = {
    help: false,
    byteLength: DEFAULT_BYTE_LENGTH,
    encoding: DEFAULT_ENCODING,
    byteLengthSet: false,
    encodingSet: false,
  };
  const positional = [];

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];

    if (arg === '-h' || arg === '--help') {
      result.help = true;
      continue;
    }

    if (arg === '-b' || arg === '--bytes' || arg === '-s' || arg === '--size') {
      i += 1;
      if (i >= argv.length) {
        throw new Error(`Missing value for ${arg}`);
      }

      result.byteLength = normalizeByteLength(argv[i]);
      result.byteLengthSet = true;
      continue;
    }

    if (arg.startsWith('--bytes=') || arg.startsWith('--size=')) {
      const val = arg.startsWith('--bytes=') ? arg.slice('--bytes='.length) : arg.slice('--size='.length);
      result.byteLength = normalizeByteLength(val);
      result.byteLengthSet = true;
      continue;
    }

    if (arg === '-e' || arg === '--encoding') {
      i += 1;
      if (i >= argv.length) {
        throw new Error('Missing value for --encoding');
      }

      result.encoding = normalizeEncoding(argv[i]);
      result.encodingSet = true;
      continue;
    }

    if (arg.startsWith('--encoding=')) {
      result.encoding = normalizeEncoding(arg.slice('--encoding='.length));
      result.encodingSet = true;
      continue;
    }

    if (arg.startsWith('-')) {
      throw new Error(`Unknown option: ${arg}`);
    }

    positional.push(arg);
  }

  if (positional.length > 2) {
    throw new Error('Too many positional arguments');
  }

  if (positional.length >= 1 && !result.byteLengthSet) {
    result.byteLength = normalizeByteLength(positional[0]);
  }

  if (positional.length === 2 && !result.encodingSet) {
    result.encoding = normalizeEncoding(positional[1]);
  }

  return result;
}

function runCli(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);

  if (options.help) {
    process.stdout.write(HELP_TEXT);
    return;
  }

  process.stdout.write(`${generateJWTSecret(options.byteLength, options.encoding)}\n`);
}

if (require.main === module) {
  try {
    runCli();
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}

module.exports = generateJWTSecret;
module.exports.generateJWTSecret = generateJWTSecret;
module.exports.parseArgs = parseArgs;
module.exports.runCli = runCli;
module.exports.DEFAULT_BYTE_LENGTH = DEFAULT_BYTE_LENGTH;
module.exports.DEFAULT_ENCODING = DEFAULT_ENCODING;
