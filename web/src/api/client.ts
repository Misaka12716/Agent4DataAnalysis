import axios from 'axios'

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 120_000,
  headers: {
    'Content-Type': 'application/json',
  },
})

export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { detail?: string; msg?: string } | undefined
    return data?.detail || data?.msg || error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return String(error)
}
