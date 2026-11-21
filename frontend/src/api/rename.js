import{api,post}from'./client.js';
export const list=()=>api('/renames');
export const detail=id=>api('/renames/'+id);
export const create=data=>post('/renames',data);
export const liveWrite=(id,data)=>post(`/renames/${id}/live-write`,data);
export const decide=(id,data)=>post(`/renames/${id}/decisions`,data);
export const requestSwitch=(id,attempt_id)=>post(`/renames/${id}/switch`,{attempt_id});
export const runSwitch=(id,attempt_id)=>post(`/renames/${id}/switch/run`,{attempt_id});
export const readKey=(id,key)=>api(`/renames/${id}/keys/${encodeURIComponent(key)}`);
