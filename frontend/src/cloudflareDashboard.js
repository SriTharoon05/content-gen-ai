// Options supported by the native Cloudflare channel/settings contract.
export const languageOptions = [
  ['en','English'],['ta','Tamil'],['te','Telugu'],['ml','Malayalam'],['kn','Kannada'],['hi','Hindi'],
  ['mr','Marathi'],['bn','Bengali'],['gu','Gujarati'],['pa','Punjabi'],['ur','Urdu'],['ar','Arabic'],
  ['id','Indonesian'],['ms','Malay'],['th','Thai'],['vi','Vietnamese'],['fil','Filipino'],['ja','Japanese'],
  ['ko','Korean'],['cmn','Mandarin'],['es','Spanish'],['pt','Portuguese'],['fr','French'],['de','German'],
  ['it','Italian'],['nl','Dutch'],['pl','Polish'],['ru','Russian'],['tr','Turkish'],['uk','Ukrainian'],
].map(([value,label]) => ({value,label}))

export const geminiVoiceOptions = 'Zephyr Puck Charon Kore Fenrir Leda Orus Aoede Callirrhoe Autonoe Enceladus Iapetus Umbriel Algieba Despina Erinome Algenib Rasalgethi Laomedeia Achernar Alnilam Schedar Gacrux Pulcherrima Achird Zubenelgenubi Vindemiatrix Sadachbia Sadaltager Sulafat'.split(' ').map(value=>({value,label:value}))

export function speedDurationValid(duration, rate) {
  return Number.isFinite(duration) && Number.isFinite(rate) && rate >= .75 && rate <= 1.25 && duration / rate >= 45 && duration / rate <= 90
}

export function analyticsTable(platform, report) {
  if (platform === 'youtube') return {
    columns: (report?.columnHeaders || []).map(column => column.name.replaceAll('_',' ')),
    rows: report?.rows || [],
  }
  return {
    columns: ['Metric', 'Value'],
    rows: (report?.data || []).map(metric => {
      const total = metric.total_value?.value
      const values = metric.values || []
      const value = total ?? (values.length && values.every(item => typeof item.value === 'number')
        ? values.reduce((sum,item) => sum + item.value, 0) : values.at(-1)?.value) ?? 'Unavailable'
      return [metric.title || metric.name?.replaceAll('_',' ') || 'Metric', typeof value === 'object' ? JSON.stringify(value) : value]
    }),
  }
}
