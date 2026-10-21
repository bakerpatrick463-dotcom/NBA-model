"""Read source table labels; never resolve player/event identities by name alone."""
from bisect import bisect_right
from datetime import datetime
import re

from .injuries import positioned_lines

STATUSES={'Out','Doubtful','Questionable','Probable','Available'}
NOTE=('Source report labels only. Player and event IDs are unresolved. Not submitted '
      'does not mean healthy. Missing player entries do not imply availability.')


def words(pieces):
    return ' '.join(text for _,text in sorted(pieces))


def columns(pieces,starts):
    cells=[[] for _ in starts]
    for x,text in sorted(pieces):
        index=bisect_right(starts,x)-1
        if index<0:
            raise ValueError('Text outside recognized table columns')
        cells[index].append(text)
    return [' '.join(cell) for cell in cells]


def report_rows(pages):
    """Fail closed if headers, row boundaries or continuation context are unclear.

    Player/status baselines anchor each source row. Wrapped reason lines are
    grouped by the closest row baseline, never simply attached to the preceding
    player in reading order. Date, time, matchup and team labels may span rows
    and pages. Nothing here establishes a canonical roster or fixture identity.
    """
    try:
        return _report_rows(pages)
    except (ValueError,KeyError,TypeError,IndexError) as error:
        return {'status':'unrecognized_layout','player_status_count':0,
                'not_submitted_count':0,'rows':[], 'reason':str(error),'note':NOTE}


def _report_rows(pages):
    if not pages or not pages[0].get('fragments'):
        raise ValueError('Positioned source words are required for row extraction')
    first=positioned_lines(pages[0]['fragments'])
    headers=[(y,p) for y,p in first if words(p)=='Game Date Game Time Matchup Team Player Name Current Status Reason']
    if len(headers)!=1:
        raise ValueError('Expected one complete seven-column report header')
    header_y,header=headers[0]
    names=['Game','Game','Matchup','Team','Player','Current','Reason']
    starts=[]
    remaining=sorted(header)
    for name in names:
        index=next((i for i,(_,text) in enumerate(remaining) if text==name),None)
        if index is None:
            raise ValueError('Report column identity is ambiguous')
        starts.append(remaining[index][0]-3)
        remaining=remaining[index+1:]
    if starts!=sorted(starts) or len(set(starts))!=7:
        raise ValueError('Report columns are not ordered')
    rows=[]
    context={'game_date':None,'game_time_label':None,'matchup':None,'team':None}
    for page in pages:
        lines=positioned_lines(page['fragments'])
        titles=[y for y,p in lines if re.fullmatch(r'Injury Report: \d{2}/\d{2}/\d{2} \d{2}:\d{2} [AP]M',words(p))]
        footers=[y for y,p in lines if words(p)==f'Page {page["page"]} of {len(pages)}']
        if len(titles)!=1 or len(footers)!=1:
            raise ValueError('Report page identity is ambiguous')
        top=header_y if page['page']==pages[0]['page'] else titles[0]-5
        body=[(y,columns(p,starts)) for y,p in lines if footers[0]+5<y<top-2]
        anchors=[(y,c) for y,c in body if c[4] or c[6]=='NOT YET SUBMITTED']
        if not anchors:
            raise ValueError('No recognizable player or unsubmitted-report rows')
        bands=[[] for _ in anchors]
        for y,cells in body:
            distances=sorted((abs(y-baseline),i) for i,(baseline,_) in enumerate(anchors))
            if distances[0][0]>18 or (len(distances)>1 and distances[1][0]-distances[0][0]<2):
                raise ValueError('Wrapped source text cannot be assigned to one row confidently')
            bands[distances[0][1]].append((y,cells))
        for (baseline,_),band in zip(anchors,bands):
            cells=[' '.join(c[i] for _,c in band if c[i]) for i in range(7)]
            date,time,matchup,team,player,status,reason=cells
            if date:
                source_date=datetime.strptime(date,'%m/%d/%Y').date().isoformat()
                if source_date!=context['game_date']:
                    context={'game_date':source_date,'game_time_label':None,'matchup':None,'team':None}
            if time:
                if not re.fullmatch(r'\d{2}:\d{2} \(ET\)',time):
                    raise ValueError('Unrecognized source game-time label')
                datetime.strptime(time[:5],'%H:%M')
                context['game_time_label']=time
            if matchup:
                if not re.fullmatch(r'[A-Z]{2,3}@[A-Z]{2,3}',matchup):
                    raise ValueError('Unrecognized source matchup label')
                context['matchup']=matchup
                context['team']=None
            if team:
                context['team']=team
            if not all(context.values()):
                raise ValueError('Source row lacks date, time, matchup or team context')
            unsubmitted=reason=='NOT YET SUBMITTED'
            if unsubmitted:
                if player or status:
                    raise ValueError('Unsubmitted team report unexpectedly contains player claims')
            elif not re.fullmatch(r'[^,]+,\s*\S.*',player) or status not in STATUSES or not reason:
                raise ValueError('Player, status or reason cell is incomplete or unsupported')
            # Rejoin words broken at source hyphens, e.g. Two- Way. Other
            # spelling and status labels remain as printed in the source.
            reason=re.sub(r'(?<=\w)-\s+(?=\w)','-',reason)
            rows.append({**context,'record_type':'team_report_not_submitted' if unsubmitted else 'player_status',
                         'player_name':player or None,'status':status or None,'reason':reason,
                         'source_page':page['page'],'source_row':len(rows)+1,'source_baseline_y':round(baseline,3),
                         'player_id':None,'event_id':None,'identity_match_status':'unverified'})
    players=[row for row in rows if row['record_type']=='player_status']
    keys=[(row['game_date'],row['matchup'],row['team'],row['player_name']) for row in players]
    if len(keys)!=len(set(keys)):
        raise ValueError('Duplicate player source rows require review')
    return {'status':'parsed_source_labels','player_status_count':len(players),
            'not_submitted_count':len(rows)-len(players),'rows':rows,'note':NOTE}
