import{list,detail}from'../api/rename.js';
export const renames={items:[],current:null,
  async load(){this.items=await list();return this.items},
  async open(id){this.current=await detail(id);return this.current}};
