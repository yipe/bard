import { Component, type ErrorInfo, type ReactNode } from "react"
import { Button } from "@/components/ui/button"

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="flex h-dvh flex-col items-center justify-center gap-4 p-6 text-center">
        <h1 className="text-lg font-semibold">Something broke — reload</h1>
        <pre className="max-w-xl overflow-auto rounded-md bg-muted p-3 text-left text-xs whitespace-pre-wrap text-muted-foreground">
          {error.message || String(error)}
        </pre>
        <Button onClick={() => location.reload()}>Reload</Button>
      </div>
    )
  }
}
