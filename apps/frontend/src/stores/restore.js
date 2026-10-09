import { defineStore } from 'pinia'
import * as restores from 'src/api/restores'

export const useRestoreStore = defineStore('restore', () => ({ ...restores }))
