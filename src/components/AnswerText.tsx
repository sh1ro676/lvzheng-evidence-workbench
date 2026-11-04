interface AnswerTextProps {
  text: string
  onCitation: (citationId: number) => void
}

export function AnswerText({ text, onCitation }: AnswerTextProps) {
  return (
    <div className="answer-text">
      {text.split('\n').map((paragraph, paragraphIndex) => (
        <p key={paragraphIndex}>
          {paragraph.split(/(【证据\s*\d+\s*】)/g).map((part, index) => {
            const match = part.match(/【证据\s*(\d+)\s*】/)
            if (!match) return part
            const citationId = Number(match[1])
            return (
              <button
                type="button"
                className="inline-citation"
                key={index}
                onClick={() => onCitation(citationId)}
                aria-label={`查看证据 ${citationId}`}
              >
                {citationId}
              </button>
            )
          })}
        </p>
      ))}
    </div>
  )
}
