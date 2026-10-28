"use strict";
document.getElementById("login-form").addEventListener("submit",async event=>{
  event.preventDefault();
  const button=document.getElementById("enter"),error=document.getElementById("error");
  button.disabled=true;error.hidden=true;
  try{
    const response=await fetch("/api/team/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:document.getElementById("code").value})});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||"Не удалось войти.");
    document.getElementById("code").value="";
    location.replace("/");
  }catch(cause){error.textContent=cause.message||"Нет связи с тренажёром. Попробуйте ещё раз.";error.hidden=false;}
  finally{button.disabled=false;}
});
