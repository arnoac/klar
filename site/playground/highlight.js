// Klar syntax highlighting for the playground editor: the same rules as site/highlight.klar, which colours
// the code blocks on the rest of the site. Returns HTML with every piece of source text escaped.

const KEYWORDS = new Set(["fn", "let", "var", "if", "then", "else", "while", "for", "in", "return", "break", "continue",
  "match", "struct", "enum", "uses", "true", "false", "and", "or", "not", "assert", "test", "with", "use", "extern",
  "type", "trait", "impl", "io"]);

const escape = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const isDigit = c => c >= "0" && c <= "9";
const isWord = c => /[A-Za-z0-9_]/.test(c);

export function highlight(src) {
  let out = "";
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    let j = i + 1;
    let kind = "";
    if (c === "/" && src[j] === "/") {
      while (j < n && src[j] !== "\n") j++;
      kind = "com";
    } else if (c === '"' || (c === "r" && src[j] === '"')) {
      if (c === "r") j++;
      if (src[j] === '"' && src[j + 1] === '"') {
        j += 2;
        while (j < n && !(src[j] === '"' && src[j + 1] === '"' && src[j + 2] === '"')) j++;
        j = Math.min(j + 3, n);
      } else {
        while (j < n && src[j] !== '"' && src[j] !== "\n") {
          if (src[j] === "\\") j++;
          j++;
        }
        j = Math.min(j + 1, n);
      }
      kind = "str";
    } else if (isDigit(c)) {
      while (j < n && (isWord(src[j]) || (src[j] === "." && isDigit(src[j + 1] || "")))) j++;
      kind = "num";
    } else if (isWord(c)) {
      while (j < n && isWord(src[j])) j++;
      const word = src.slice(i, j);
      if (KEYWORDS.has(word)) kind = "kw";
      else if (c >= "A" && c <= "Z") kind = "type";
      else if ((src[j] === "(" || src[j] === "[") && src[i - 1] !== ".") kind = "fn";
    }
    const piece = escape(src.slice(i, j));
    out += kind ? `<span class="tok-${kind}">${piece}</span>` : piece;
    i = j;
  }
  return out;
}
