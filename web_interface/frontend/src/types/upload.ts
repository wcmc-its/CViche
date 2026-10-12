export interface Estimate {
  document_tokens: number
  text_characters: number
  estimated_cost_min: number | null
  estimated_cost_max: number | null
  estimated_time_seconds_min: number
  estimated_time_seconds_max: number
  num_steps: number
  filename: string
  file_size_kb: number
  pricing_model: string | null
  text_characters_is_guess: boolean
  scanned_pages?: number[]
  /** Estimated cost in typical CVs (#1599), shown to every role; absent from a backend that predates it. */
  cost_weight?: number
}
