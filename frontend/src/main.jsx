import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import App from "./App.jsx"
import "./index.css"

// Cache global do React Query.
// - staleTime alto: a Home fica "fresca" por minutos, entao FECHAR o modal e
//   voltar NAO dispara refetch -> sem flicker, sem re-download de capas.
// - gcTime alto: o cache sobrevive mesmo sem componentes observando a query.
// - refetchOnWindowFocus off: focar a janela nao refaz a busca.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 min
      gcTime: 30 * 60 * 1000, // 30 min
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      retry: 1,
    },
  },
})

createRoot(document.getElementById("root")).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>,
)
