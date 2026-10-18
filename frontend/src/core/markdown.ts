import MarkdownIt from 'markdown-it';

// Builder-authored prose is data: never execute HTML, Vue, or scripts.
const markdown = new MarkdownIt({ html: false, linkify: true });

export const renderMarkdown = (source: string): string => markdown.render(source);
