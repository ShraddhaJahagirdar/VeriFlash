import datetime
import uuid
from fastapi import HTTPException, Depends
import jwt
from sqlalchemy.orm import Session
from sqlalchemy import text, exc
from . import models
from . import schemas
import passlib.hash as hash
from fastapi.security import OAuth2PasswordBearer
import vsr_log


def saveECUScanResults(db: Session, ECUScanResults):
    esr: schemas.Ecu_scanCreate
    for esr in ECUScanResults:
        db_ecuscandata = models.Ecu_scan(id=uuid.uuid4(), ecu_name=esr.ecu_name, vin=esr.vin, sign_found=esr.sign_found,
                                         sign_ref=esr.sign_ref, filename=esr.filename, verified_status=esr.verified_status,
                                         verified=esr.verified, verified_ts=esr.verified_ts, flash_error=esr.flash_error,
                                         project_id=esr.project_id, vin_error=esr.vin_error)
        try:
            db.add(db_ecuscandata)
            db.commit()
        except exc.IntegrityError:
            vsr_log.vsrQueryRollback(esr.filename)
            vsr_log.vsrInfo('\n')
            db.rollback()
            continue


################Project##########################


def get_project(db: Session, project_id: uuid.UUID):
    return db.query(models.Project).filter(models.Project.id == project_id).first()


def get_project_list(db: Session, filter: str):
    if filter == 'all':
        return db.query(models.Project).all()
    else:
        return db.query(models.Project).filter(models.Project.company_name == filter).all()


def create_project(db: Session, project: schemas.Project):
    db_project = models.Project(id=uuid.uuid4(), company_name=project.company_name, vehicle_name=project.vehicle_name,
                                location=project.location, create_ts=project.create_ts, status="In Progress",
                                vin_interpret=project.vin_interpret, file_format=project.file_format,
                                file_location=project.file_location)
    try:
        db.add(db_project)
        db.commit()
        db.refresh(db_project)
    except exc.IntegrityError:
        db.rollback()

    return db_project

#################ReferenceData####################


def save_reference_data(db: Session, refData, project_id: uuid.UUID):

    db.query(models.Reference).filter(models.Reference.project_id ==
                                      project_id).delete()  # remove all old ref_data
    ref: schemas.ReferenceCreate

    # add new ref data
    for record in range(len(refData)):
        refData.loc[record, "project_id"] = project_id
        ref = refData.loc[record]
        db_ref = models.Reference(ecu_name=ref.ecu_name,
                                  ecu_signature=ref.ecu_signature, parameter_name=ref.parameter_name,
                                  verification_method=ref.verification_method, tag_1=ref.tag_1, tag_2=ref.tag_2,
                                  tag_interpret=ref.tag_interpret, project_id=ref.project_id)
        try:
            db.add(db_ref)
            db.commit()
        except exc.IntegrityError:
            db.rollback()
            continue
    # remove old data from ecu_Scan table since the ref data is changed
    db.query(models.Ecu_scan).filter(
        models.Ecu_scan.project_id == project_id).delete()
    db.commit()


def get_reference_data(db: Session, project_id: uuid.UUID):
    return db.query(models.Reference).filter(models.Reference.project_id == project_id).order_by(models.Reference.ecu_name).all()

#####################VehiclaScanReport#############################


def get_lastECUProcessedTS(db: Session, project_id: uuid.UUID) -> schemas.Ecu_scan:
    return db.query(models.Ecu_scan).filter(models.Ecu_scan.project_id == project_id).order_by(models.Ecu_scan.verified_ts.desc()).first()


#################################Flash-Stats###################################


def get_flash_stats(db: Session, project_id):
    flashStats = []
    # Sequence of results in the select should be maintained in the schemas.FlashStats
    statement = text('''select 	V.filename,V.vin as id, V.verified,
                                case when P.passed is null THEN 0 else P.passed END,
                                case when F.failed is null THEN 0 else F.failed END,
                                F_ECU.Failed_ECUs, IF_ECU.Incorrectly_Flashed, VM_ECU.Vin_mismatch
                                from
                                    (select filename, vin, count(verified) as Verified
                                    from ecu_scan
                                    where verified = true and project_id = :project_id
                                    group by filename , vin
                                    ) as V
                        left outer join (
                                    select vin, count(verified) as failed
                                    from ecu_scan
                                    where verified = true and verified_status = 'Fail' and project_id = :project_id
                                    group by vin
                                    ) as F on V.vin = F.vin
                        left outer join (
                                    select vin, count(verified) as passed
                                    from ecu_scan
                                    where verified = true and verified_status = 'OK' and project_id = :project_id
                                    group by vin
                                    ) as P on V.vin = P.vin
                        left outer join (
                                    SELECT vin, STRING_AGG(ecu_name, ', ') AS Failed_ECUs
                                    FROM ecu_scan
                                    where verified_status = 'Fail' and project_id = :project_id
                                    GROUP BY vin
                                    )as F_ECU on V.vin = F_ECU.vin
                        left outer join (
                                    SELECT vin, STRING_AGG(ecu_name, ', ') AS Incorrectly_Flashed
                                    FROM ecu_scan
                                    where verified_status = 'Fail' and flash_error != '' and project_id = :project_id
                                    GROUP BY vin
                                    )as IF_ECU on V.vin = IF_ECU.vin
                        left outer join (
                                    SELECT vin, vin_error AS Vin_mismatch 
                                    FROM ecu_scan 
                                    where project_id = :project_id  
                                    GROUP BY vin, vin_error 
                                    ORDER BY vin
                                    )as VM_ECU on P.vin = VM_ECU.vin 
                                    order by F.failed desc, F_ECU.Failed_ECUs desc''')
    result = db.execute(statement, {"project_id": project_id}).all()

    for record in result:
        flashStats.append(schemas.FlashStats(**record).dict())
    return flashStats


#########################SIGN_IN-SIGN_UP##################################################
oauth2schema = OAuth2PasswordBearer(tokenUrl="/create-token")

JWT_SECRET = "crudJWTSecret"


def get_user_by_username(username: str, db: Session):
    return db.query(models.User).filter(models.User.username == username).first()


def create_user(user: schemas.UserCreate, db: Session):
    user_obj = models.User(id=uuid.uuid4(),
                           username=user.username, password=hash.bcrypt.hash(
        user.password), company_name=user.company_name
    )
    db.add(user_obj)
    db.commit()
    db.refresh(user_obj)
    return user_obj


def authenticate_user(username: str, password: str, db: Session):
    user = get_user_by_username(db=db, username=username)

    if not user:
        return False

    if not user.verify_password(password):
        return False

    return user


def create_token(user: models.User):
    user_obj = schemas.User.from_orm(user)

    exp_time = datetime.datetime.now() + datetime.timedelta(seconds=3600)

    user_obj = user_obj.dict()
    user_obj['exp'] = exp_time.timestamp()

    token = jwt.encode(user_obj, JWT_SECRET)

    return dict(access_token=token, token_type="bearer")


def refresh_token(token: str = Depends(oauth2schema)):
    try:
        exp_time = datetime.datetime.now() + datetime.timedelta(seconds=3600)
        prevToken = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        prevToken['exp'] = exp_time.timestamp()

        refreshToken = jwt.encode(prevToken, JWT_SECRET)
    except:
        raise HTTPException(
            status_code=401, detail="Could not decode the user. Invalid token."
        )

    return refreshToken


################################USER-QUERIES######################################
def get_company_by_username(username: str, db: Session):
    return db.query(models.User.company_name).filter(models.User.username == username).first()


def get_all_companies(db: Session):
    result = [tup[0] for tup in db.query(models.User.company_name).all()]
    return result
