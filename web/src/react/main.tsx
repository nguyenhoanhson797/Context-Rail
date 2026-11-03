import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import './styles.css';
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 15000, refetchOnWindowFocus: false } } });
const root = document.getElementById('app');
if (!root) throw new Error('Context Rail application root is missing');
createRoot(root).render(<QueryClientProvider client={queryClient}><App /></QueryClientProvider>);
