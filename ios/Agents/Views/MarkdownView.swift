import SwiftUI

/// Agent replies are markdown. `Text` renders only inline markdown, so blocks are parsed here.
enum MarkdownBlock: Hashable {
    case paragraph(String)
    case heading(String)
    case list(ordered: Bool, items: [String])
    case code(String)
    case quote(String)
    case table([[String]])

    static func parse(_ source: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var list: (ordered: Bool, items: [String])?
        let lines = source.replacingOccurrences(of: "\r", with: "").components(separatedBy: "\n")

        func endParagraph() {
            if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: "\n"))) }
            paragraph = []
        }
        func endList() {
            if let open = list { blocks.append(.list(ordered: open.ordered, items: open.items)) }
            list = nil
        }

        var index = 0
        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            defer { index += 1 }
            if trimmed.hasPrefix("```") {
                endParagraph(); endList()
                var code: [String] = []
                index += 1
                while index < lines.count, !lines[index].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    code.append(lines[index]); index += 1
                }
                blocks.append(.code(code.joined(separator: "\n")))
                continue
            }
            if trimmed.hasPrefix("|"), index + 1 < lines.count, lines[index + 1].contains("---") {
                endParagraph(); endList()
                func cells(_ row: String) -> [String] {
                    row.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "|"))
                        .components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
                }
                var rows = [cells(line)]
                index += 1
                while index + 1 < lines.count, lines[index + 1].trimmingCharacters(in: .whitespaces).hasPrefix("|") {
                    index += 1; rows.append(cells(lines[index]))
                }
                blocks.append(.table(rows))
                continue
            }
            if let heading = trimmed.firstMatch(of: #/^#{1,4}\s+(.+)/#) {
                endParagraph(); endList()
                blocks.append(.heading(String(heading.1)))
            } else if let item = trimmed.firstMatch(of: #/^[-*]\s+(.+)/#) {
                endParagraph()
                if list?.ordered != false { endList(); list = (false, []) }
                list?.items.append(String(item.1))
            } else if let item = trimmed.firstMatch(of: #/^\d+[.)]\s+(.+)/#) {
                endParagraph()
                if list?.ordered != true { endList(); list = (true, []) }
                list?.items.append(String(item.1))
            } else if let quote = trimmed.firstMatch(of: #/^>\s?(.*)/#) {
                endParagraph(); endList()
                blocks.append(.quote(String(quote.1)))
            } else if trimmed.isEmpty {
                endParagraph(); endList()
            } else {
                endList()
                paragraph.append(line)
            }
        }
        endParagraph(); endList()
        return blocks
    }
}

func inline(_ text: String) -> AttributedString {
    (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
}

struct MarkdownView: View {
    let blocks: [MarkdownBlock]

    init(_ source: String) { blocks = MarkdownBlock.parse(source) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                switch block {
                case let .paragraph(text):
                    Text(inline(text))
                case let .heading(text):
                    Text(inline(text)).font(.headline)
                case let .list(ordered, items):
                    VStack(alignment: .leading, spacing: 5) {
                        ForEach(Array(items.enumerated()), id: \.offset) { position, item in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text(ordered ? "\(position + 1)." : "•").foregroundStyle(.secondary).monospacedDigit()
                                Text(inline(item))
                            }
                        }
                    }
                case let .code(code):
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(code).font(.system(.footnote, design: .monospaced)).padding(12)
                    }
                    .background(.fill.tertiary, in: .rect(cornerRadius: 12))
                case let .quote(text):
                    Text(inline(text)).foregroundStyle(.secondary).padding(.leading, 10)
                        .overlay(alignment: .leading) { Capsule().fill(.tertiary).frame(width: 3) }
                case let .table(rows):
                    ScrollView(.horizontal, showsIndicators: false) {
                        Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                            ForEach(Array(rows.enumerated()), id: \.offset) { number, row in
                                GridRow {
                                    ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                                        Text(inline(cell)).font(number == 0 ? .footnote.weight(.semibold) : .footnote)
                                    }
                                }
                                if number == 0 { Divider() }
                            }
                        }
                        .padding(12)
                    }
                    .background(.fill.quaternary, in: .rect(cornerRadius: 12))
                }
            }
        }
        .textSelection(.enabled)
    }
}
