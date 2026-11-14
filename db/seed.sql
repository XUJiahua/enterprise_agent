INSERT INTO "Purchase" (id,"tenantId","createdBy",title,amount,status) VALUES
 ('a-own','tenant-a','alice','Alice laptop',100,'submitted'),
 ('a-self','tenant-a','bob','Self approval fixture',200,'submitted'),
 ('a-other','tenant-a','bob','Bob laptop',200,'submitted'),
 ('a-done','tenant-a','bob','Approved laptop',300,'approved'),
 ('b-other','tenant-b','dave','Dave laptop',400,'submitted');
