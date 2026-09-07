import { Component } from "react"
import { clearBrowserStorage } from "./browserStorage.js"

export default class AppErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error) {
    console.error("Kari UI error", error)
  }

  reload = () => {
    window.location.reload()
  }

  reset = () => {
    clearBrowserStorage()
    window.location.reload()
  }

  render() {
    if (!this.state.failed) return this.props.children

    return (
      <main className="grid min-h-screen place-items-center bg-app px-5 text-zinc-100">
        <section className="w-full max-w-md rounded-md border border-line bg-panel p-6 text-center shadow-2xl">
          <h1 className="text-xl font-black">O Kari encontrou um erro local</h1>
          <p className="mt-2 text-sm text-zinc-400">
            Recarregue a interface. Se continuar, limpe apenas os dados locais deste site.
          </p>
          <div className="mt-5 grid gap-2 sm:grid-cols-2">
            <button type="button" onClick={this.reload} className="h-10 rounded border border-line font-bold hover:border-zinc-500">
              Recarregar
            </button>
            <button type="button" onClick={this.reset} className="h-10 rounded bg-accent font-black text-app hover:bg-emerald-200">
              Limpar e recuperar
            </button>
          </div>
        </section>
      </main>
    )
  }
}
