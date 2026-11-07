"use strict";
(async () => {
  const $=id=>document.getElementById(id);
  const make=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
  const get=async url=>{const response=await fetch(url,{cache:"no-store"});if(!response.ok)throw new Error("无法读取 "+url);return response.json();};
  try{
    const [data,map]=await Promise.all([get("data.json"),get("test-map.json")]);
    const tabs=[];
    function showCase(item,index){
      tabs.forEach((tab,i)=>tab.setAttribute("aria-selected",String(i===index)));
      $("case-title").textContent=item.title;$("case-purpose").textContent=item.purpose;
      $("captured-at").textContent="本机实际运行 · "+new Date(data.captured_at).toLocaleString("zh-CN");
      $("input").textContent=item.input;$("command").textContent=item.after.command_display;
      for(const version of ["before","after"]){
        const result=item[version],badge=$(version+"-badge");
        badge.textContent=result.passed?"符合本例预期":"未满足本例预期";badge.className="badge "+(result.passed?"good":"bad");
        $(version+"-exit").textContent="实际退出码 "+result.exit_code+" · "+result.duration_s.toFixed(2)+" 秒";
        $(version+"-output").textContent=(result.stdout+(result.stderr?"\n[stderr]\n"+result.stderr:"")).trimEnd();
        const links=$(version+"-links");links.replaceChildren();
        for(const [key,label] of [["stdout_url","原始 stdout"],["stderr_url","原始 stderr"],["svg_url","SVG 导出"],["html_url","HTML 导出"]]){
          if(!result[key])continue;const link=make("a",label);link.href=result[key];link.target="_blank";link.rel="noopener";links.append(link);
        }
      }
      $("svg-output").src=item.after.svg_url||"";
      $("svg-output").hidden=!item.after.svg_url;
    }
    data.cases.forEach((item,index)=>{const button=make("button",["CSV 字面值","异常退出","TSV","格式打印","JSON","Markdown"][index]);button.type="button";button.setAttribute("role","tab");button.setAttribute("aria-selected","false");button.addEventListener("click",()=>showCase(item,index));tabs.push(button);$("scenario-tabs").append(button);});
    showCase(data.cases[0],0);
    $("demo-evidence").textContent="本页附加演示实际执行 "+data.cases.length*2+" 次原版/修复版命令；修复版 "+data.cases.filter(item=>item.after.passed).length+"/"+data.cases.length+" 满足本例要求。演示新增模型调用 0 次；不改写原实验 15/21/16 的测试计数。";
    for(const item of map.generated_cases){
      const row=make("tr");row.append(make("td",item.title));
      const input=make("td");input.append(make("pre",typeof item.input==="string"?item.input:JSON.stringify(item.input,null,2)));row.append(input,make("td",item.expected));$("test-cases").append(row);
    }
    for(const [key,title] of [["independent_groups","21 项独立验收"],["regression_groups","13 项自建冻结回归"]]){
      const card=make("article");card.append(make("h3",title));const list=make("ul");for(const group of map[key])list.append(make("li",group.title+"："+group.count+" 项"));card.append(list);$("coverage-groups").append(card);
    }
    for(const stage of map.llm_stages){const row=make("li");row.append(make("strong",stage.title),make("p",stage.action));$("llm-stages").append(row);}
    const verification=make("li");verification.append(make("strong","第 9 阶段：宿主确定性验证"),make("p","实际执行冻结测试、独立验收、wheel 构建与真实 CLI 进程；核对候选源码、测试封印和评审哈希，全部通过才接受交付。"));$("llm-stages").append(verification);
    const grid=make("div",undefined,"scores"),score=data.experiment.scores;
    for(const [label,key]of[["模型生成测试","generated"],["独立验收","acceptance"],["上游原有测试","upstream_regression"],["冻结行为回归","behavior_regression"],["真实 CLI","extended"]]){const item=make("div",undefined,"score");item.append(make("span",label),make("strong",score[key].passed+" / "+score[key].total));grid.append(item);}
    $("score-summary").append(grid);
    $("model-note").textContent="成功实验 "+data.experiment.duration_s+" 秒，"+data.experiment.model_calls+" 次真实请求，指定 "+data.experiment.model_requested+"。服务没有返回实际模型名。模型生成测试与代码，宿主执行 pytest、构建和真实 CLI。";
    $("report-link").href="/api/runs/"+data.model_run_id+"/report?format=md";
    for(const text of map.not_covered)$("scope-limits").append(make("li",text));
    $("source-note").textContent="固定 GitHub 提交 "+data.source.commit.slice(0,12)+" · 模型运行 "+data.model_run_id.slice(0,8);
  }catch(error){$("error").hidden=false;$("error").textContent=error.message;}
})();
