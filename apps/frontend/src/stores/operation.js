import { defineStore } from 'pinia'
import * as operations from 'src/api/operations'

// Keep the public store entry point used by dialogs; operations themselves have no shared state.
export const useOperationStore = defineStore('operation', () => ({ ...operations }))
