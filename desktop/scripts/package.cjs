const fs=require('node:fs');
const path=require('node:path');
const {packager}=require('@electron/packager');
(async()=>{
  const directory=path.resolve(__dirname,'..');
  const version=require('../package.json').version;
  const destination=path.resolve(directory,'../dist/desktop',version);
  if(fs.existsSync(path.join(destination,'Wudun-TestFix-win32-x64')))throw new Error('此版本已存在，请更新版本号后构建，避免覆盖用户正在使用的软件。');
  const result=await packager({dir:directory,name:'Wudun-TestFix',platform:'win32',arch:'x64',out:destination,asar:true,overwrite:false,prune:true,electronZipDir:path.join(directory,'.runtime-cache'),ignore:[/^\/test($|\/)/,/^\/scripts($|\/)/,/^\/node_modules($|\/)/,/^\/\.runtime-cache($|\/)/,/^\/pnpm-lock\.yaml$/,/^\/pnpm-workspace\.yaml$/],icon:path.join(directory,'assets/icon.ico'),win32metadata:{CompanyName:'TestFix Team',ProductName:'无遁 TestFix',FileDescription:'无遁多智能体桌面工作台',OriginalFilename:'Wudun-TestFix.exe'}});
  for(const output of result){fs.writeFileSync(path.join(output,'使用说明.txt'),'双击 Wudun-TestFix.exe 启动。\r\n这是本机工程版，首次使用会自动识别工程，也可在软件右上角选择工程文件夹。\r\n请保留整个软件文件夹和原工程目录，不要只移动 exe。\r\n无需启动浏览器或手动运行本地服务。大模型与云端功能按现有配置联网调用。\r\n');console.log(output);}
})().catch(error=>{console.error(error);process.exitCode=1;});
